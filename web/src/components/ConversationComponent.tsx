"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ConnectionStatusPanel } from "@/components/ConnectionStatusPanel";
import {
	type ConnectionIssue,
	getConversationIssueSeverity,
} from "@/components/ConversationErrorCard";
import { MicrophoneSelector } from "@/components/MicrophoneSelector";
import { QuickstartConversationLayout } from "@/components/QuickstartConversationLayout";
import {
	type QuickstartAgentMetric,
	QuickstartPipelineMetrics,
} from "@/components/QuickstartPipelineMetrics";
import { QuickstartTranscriptPanel } from "@/components/QuickstartTranscriptPanel";
import { RecentTasksPanel } from "@/components/RecentTasksPanel";
import { TextMessageComposer } from "@/components/TextMessageComposer";
import { DEFAULT_AGENT_UID } from "@/lib/agora";
import {
	getCurrentInProgressMessage,
	getMessageList,
	mapAgentVisualizerState,
	normalizeTimestampMs,
	normalizeTranscript,
} from "@/lib/conversation";
import { createTextMessagePayload } from "@/lib/text-message";
import type { ConversationComponentProps } from "@/types/conversation";
import {
	type AgentState,
	type AgentTranscription,
	AgoraVoiceAI,
	AgoraVoiceAIEvents,
	MessageSalStatus,
	type TranscriptHelperItem,
	TranscriptHelperMode,
	type UserTranscription,
} from "agora-agent-client-toolkit";
import { AgentVisualizer } from "agora-agent-uikit";
import { MicButtonWithVisualizer } from "agora-agent-uikit/rtc";
import {
	RemoteUser,
	type UID,
	useClientEvent,
	useJoin,
	useLocalMicrophoneTrack,
	usePublish,
	useRTCClient,
	useRemoteUsers,
} from "agora-rtc-react";
import { setParameter } from "agora-rtc-sdk-ng/esm";

const MAX_CONNECTION_ISSUES = 6;

type RtmMessageErrorPayload = {
	object: "message.error";
	module?: string;
	code?: number;
	message?: string;
	send_ts?: number;
};

type RtmSalStatusPayload = {
	object: "message.sal_status";
	status?: string;
	timestamp?: number;
};

function isRtmMessageErrorPayload(
	value: unknown,
): value is RtmMessageErrorPayload {
	return (
		!!value &&
		typeof value === "object" &&
		(value as { object?: unknown }).object === "message.error"
	);
}

function isRtmSalStatusPayload(value: unknown): value is RtmSalStatusPayload {
	return (
		!!value &&
		typeof value === "object" &&
		(value as { object?: unknown }).object === "message.sal_status"
	);
}

export default function ConversationComponent({
	agoraData,
	rtmClient,
	onTokenWillExpire,
	onEndConversation,
}: ConversationComponentProps) {
	const client = useRTCClient();
	const remoteUsers = useRemoteUsers();
	const aiRef = useRef<AgoraVoiceAI | null>(null);
	const [isEnabled, setIsEnabled] = useState(true);
	const [isAgentConnected, setIsAgentConnected] = useState(false);
	const [isTextMessagingReady, setIsTextMessagingReady] = useState(false);
	const [messageDraft, setMessageDraft] = useState("");
	const [isTextSending, setIsTextSending] = useState(false);
	const [textSendError, setTextSendError] = useState<string | null>(null);
	const [isConnectionDetailsOpen, setIsConnectionDetailsOpen] = useState(false);

	const [connectionState, setConnectionState] = useState<string>("CONNECTING");
	const agentUID =
		agoraData.agentUid ??
		process.env.NEXT_PUBLIC_AGENT_UID ??
		String(DEFAULT_AGENT_UID);
	const [joinedUID, setJoinedUID] = useState<UID>(0);

	const [rawTranscript, setRawTranscript] = useState<
		TranscriptHelperItem<Partial<UserTranscription | AgentTranscription>>[]
	>([]);
	const [agentState, setAgentState] = useState<AgentState | null>(null);
	const [agentMetrics, setAgentMetrics] = useState<QuickstartAgentMetric[]>([]);
	const [connectionIssues, setConnectionIssues] = useState<ConnectionIssue[]>(
		[],
	);
	const addConnectionIssue = useCallback((issue: ConnectionIssue) => {
		setConnectionIssues((prev) => {
			const isDuplicate = prev.some(
				(x) =>
					x.agentUserId === issue.agentUserId &&
					x.code === issue.code &&
					x.message === issue.message &&
					Math.abs(x.timestamp - issue.timestamp) < 1500,
			);
			if (isDuplicate) return prev;
			return [issue, ...prev].slice(0, MAX_CONNECTION_ISSUES);
		});
	}, []);

	useEffect(() => {
		if (connectionIssues.length > 0) {
			setIsConnectionDetailsOpen(true);
		}
	}, [connectionIssues.length]);

	const [isReady, setIsReady] = useState(false);
	useEffect(() => {
		let cancelled = false;
		const id = setTimeout(() => {
			if (!cancelled) setIsReady(true);
		}, 0);
		return () => {
			cancelled = true;
			clearTimeout(id);
			setIsReady(false);
		};
	}, []);

	const appId = agoraData.appId ?? "";

	const { isConnected: joinSuccess } = useJoin(
		{
			appid: appId,
			channel: agoraData.channel,
			token: agoraData.token,
			uid: Number.parseInt(agoraData.uid, 10),
		},
		isReady,
	);

	const { localMicrophoneTrack } = useLocalMicrophoneTrack(isReady);

	useEffect(() => {
		if (!client) return;
		try {
			setParameter("ENABLE_AUDIO_PTS", true);
		} catch (error) {
			console.warn("Could not set ENABLE_AUDIO_PTS:", error);
		}
	}, [client]);

	useEffect(() => {
		if (joinSuccess && client) {
			const uid = client.uid;
			if (uid !== null && uid !== undefined) {
				setJoinedUID(uid);
			}
		}
	}, [joinSuccess, client]);

	useEffect(() => {
		if (!isReady || !joinSuccess) return;

		let cancelled = false;
		(async () => {
			try {
				const ai = await AgoraVoiceAI.init({
					rtcEngine: client,
					rtmConfig: { rtmEngine: rtmClient },
					renderMode: TranscriptHelperMode.TEXT,
					enableLog: true,
				});

				if (cancelled) {
					try {
						if (AgoraVoiceAI.getInstance() === ai) {
							ai.unsubscribe();
							ai.destroy();
						}
					} catch {}
					return;
				}
				aiRef.current = ai;

				ai.on(AgoraVoiceAIEvents.TRANSCRIPT_UPDATED, (t) => {
					setRawTranscript([...t]);
				});
				ai.on(AgoraVoiceAIEvents.AGENT_STATE_CHANGED, (_, event) =>
					setAgentState(event.state),
				);
				ai.on(AgoraVoiceAIEvents.AGENT_METRICS, (_, metrics) => {
					setAgentMetrics((prev) => [...prev, metrics].slice(-8));
				});
				ai.on(AgoraVoiceAIEvents.MESSAGE_ERROR, (agentUserId, error) => {
					addConnectionIssue({
						id: `${Date.now()}-${agentUserId}-message-error-${error.code}`,
						source: "rtm",
						agentUserId,
						code: error.code,
						message: error.message,
						timestamp: normalizeTimestampMs(error.timestamp),
					});
				});
				ai.on(
					AgoraVoiceAIEvents.MESSAGE_SAL_STATUS,
					(agentUserId, salStatus) => {
						if (
							salStatus.status === MessageSalStatus.VP_REGISTER_FAIL ||
							salStatus.status === MessageSalStatus.VP_REGISTER_DUPLICATE
						) {
							addConnectionIssue({
								id: `${Date.now()}-${agentUserId}-sal-${salStatus.status}`,
								source: "rtm",
								agentUserId,
								code: salStatus.status,
								message: `SAL status: ${salStatus.status}`,
								timestamp: normalizeTimestampMs(salStatus.timestamp),
							});
						}
					},
				);
				ai.on(AgoraVoiceAIEvents.AGENT_ERROR, (agentUserId, error) => {
					addConnectionIssue({
						id: `${Date.now()}-${agentUserId}-agent-error-${error.code}`,
						source: "agent",
						agentUserId,
						code: error.code,
						message: `${error.type}: ${error.message}`,
						timestamp: normalizeTimestampMs(error.timestamp),
					});
				});
				ai.subscribeMessage(agoraData.channel);
				setIsTextMessagingReady(true);
			} catch (error) {
				if (!cancelled) {
					console.error("[AgoraVoiceAI] init failed:", error);
					setTextSendError("Text messaging could not initialize. Reconnect and try again.");
				}
			}
		})();

		return () => {
			cancelled = true;
			setIsTextMessagingReady(false);
			try {
				const ai = AgoraVoiceAI.getInstance();
				if (ai) {
					ai.unsubscribe();
					ai.destroy();
				}
				if (aiRef.current === ai) aiRef.current = null;
			} catch {}
		};
	}, [
		isReady,
		joinSuccess,
		client,
		rtmClient,
		agoraData.channel,
		addConnectionIssue,
	]);

	useEffect(() => {
		const handleRtmMessage = (event: {
			message: string | Uint8Array;
			publisher: string;
		}) => {
			const payloadText =
				typeof event.message === "string"
					? event.message
					: new TextDecoder().decode(event.message);

			let parsed: unknown;
			try {
				parsed = JSON.parse(payloadText);
			} catch {
				return;
			}

			if (isRtmMessageErrorPayload(parsed)) {
				const p = parsed;
				addConnectionIssue({
					id: `${Date.now()}-${event.publisher}-rtm-msg-error-${p.code ?? "unknown"}`,
					source: "rtm-signaling",
					agentUserId: event.publisher,
					code: p.code ?? "unknown",
					message: `${p.module ?? "unknown"}: ${p.message ?? "Unknown signaling error"}`,
					timestamp: normalizeTimestampMs(p.send_ts ?? Date.now()),
				});
				return;
			}

			if (isRtmSalStatusPayload(parsed)) {
				const p = parsed;
				if (
					p.status === "VP_REGISTER_FAIL" ||
					p.status === "VP_REGISTER_DUPLICATE"
				) {
					addConnectionIssue({
						id: `${Date.now()}-${event.publisher}-rtm-sal-${p.status}`,
						source: "rtm-signaling",
						agentUserId: event.publisher,
						code: p.status,
						message: `SAL status: ${p.status}`,
						timestamp: normalizeTimestampMs(p.timestamp ?? Date.now()),
					});
				}
			}
		};

		rtmClient.addEventListener("message", handleRtmMessage);
		return () => {
			rtmClient.removeEventListener("message", handleRtmMessage);
		};
	}, [rtmClient, addConnectionIssue]);

	const transcript = useMemo(() => {
		return normalizeTranscript(rawTranscript, String(client.uid));
	}, [rawTranscript, client.uid]);

	const messageList = useMemo(() => getMessageList(transcript), [transcript]);

	const currentInProgressMessage = useMemo(() => {
		return getCurrentInProgressMessage(transcript);
	}, [transcript]);

	usePublish([localMicrophoneTrack]);

	useClientEvent(client, "user-joined", (user) => {
		if (user.uid.toString() === agentUID) setIsAgentConnected(true);
	});

	useClientEvent(client, "user-left", (user) => {
		if (user.uid.toString() === agentUID) setIsAgentConnected(false);
	});

	useEffect(() => {
		const isAgentInRemoteUsers = remoteUsers.some(
			(user) => user.uid.toString() === agentUID,
		);
		setIsAgentConnected(isAgentInRemoteUsers);
	}, [remoteUsers, agentUID]);

	useClientEvent(client, "connection-state-change", (curState) => {
		setConnectionState(curState);
	});

	const connectionSeverity = useMemo<"normal" | "warning" | "error">(() => {
		if (
			connectionState === "DISCONNECTED" ||
			connectionState === "DISCONNECTING"
		) {
			return "error";
		}
		if (
			connectionState === "CONNECTING" ||
			connectionState === "RECONNECTING"
		) {
			return "warning";
		}
		if (connectionIssues.length === 0) {
			return "normal";
		}
		return connectionIssues.some(
			(issue) => getConversationIssueSeverity(issue) === "error",
		)
			? "error"
			: "warning";
	}, [connectionState, connectionIssues]);

	const visualizerState = useMemo(
		() =>
			mapAgentVisualizerState(agentState, isAgentConnected, connectionState),
		[agentState, isAgentConnected, connectionState],
	);

	const handleMicToggle = useCallback(async () => {
		const next = !isEnabled;
		const track = localMicrophoneTrack;
		if (!track) {
			setIsEnabled(next);
			return;
		}
		try {
			await track.setEnabled(next);
			setIsEnabled(next);
		} catch (error) {
			console.error("Failed to toggle microphone:", error);
		}
	}, [isEnabled, localMicrophoneTrack]);

	const handleTextChange = useCallback((value: string) => {
		setMessageDraft(value);
		setTextSendError(null);
	}, []);

	const handleSendText = useCallback(async () => {
		const payload = createTextMessagePayload(messageDraft);
		if (!payload || isTextSending) return;

		const ai = aiRef.current;
		if (!ai || !isTextMessagingReady || !isAgentConnected) {
			setTextSendError("JARVIS is still connecting. Try again in a moment.");
			return;
		}

		setIsTextSending(true);
		setTextSendError(null);
		try {
			await ai.sendText(agentUID, payload);
			setMessageDraft("");
		} catch (error) {
			console.error("Failed to send text message:", error);
			setTextSendError("Message could not be sent. Check the connection and try again.");
			addConnectionIssue({
				id: `${Date.now()}-${agentUID}-text-send-failed`,
				source: "rtm",
				agentUserId: agentUID,
				code: "TEXT_SEND_FAILED",
				message: error instanceof Error ? error.message : "Failed to send text message",
				timestamp: Date.now(),
			});
		} finally {
			setIsTextSending(false);
		}
	}, [
		messageDraft,
		isTextSending,
		isTextMessagingReady,
		isAgentConnected,
		agentUID,
		addConnectionIssue,
	]);

	const handleTokenWillExpire = useCallback(async () => {
		if (!onTokenWillExpire || !joinedUID) return;
		try {
			const { rtcToken, rtmToken } = await onTokenWillExpire(
				joinedUID.toString(),
			);
			await client?.renewToken(rtcToken);
			await rtmClient.renewToken(rtmToken);
		} catch (error) {
			console.error("Failed to renew Agora token:", error);
		}
	}, [client, onTokenWillExpire, joinedUID, rtmClient]);

	useClientEvent(client, "token-privilege-will-expire", handleTokenWillExpire);

	const handleEndConversation = useCallback(async () => {
		const track = localMicrophoneTrack;
		if (track) {
			try {
				await client?.unpublish(track);
			} catch (error) {
				console.warn("Failed to unpublish microphone track:", error);
			}

			try {
				track.stop();
				track.close();
			} catch (error) {
				console.warn("Failed to release microphone track:", error);
			}
		}

		onEndConversation();
	}, [client, localMicrophoneTrack, onEndConversation]);

	return (
		<QuickstartConversationLayout
			statusPanel={
				<ConnectionStatusPanel
					connectionState={connectionState}
					connectionSeverity={connectionSeverity}
					connectionIssues={connectionIssues}
					isOpen={isConnectionDetailsOpen}
					onToggle={() => setIsConnectionDetailsOpen((open) => !open)}
				/>
			}
			pipelineMetrics={<QuickstartPipelineMetrics metrics={agentMetrics} />}
			transcriptPanel={
				<QuickstartTranscriptPanel
					messageList={messageList}
					currentInProgressMessage={currentInProgressMessage}
					agentUID={agentUID}
				/>
			}
			taskPanel={<RecentTasksPanel />}
			visualizer={
				<section
					className="arc-reactor-frame relative flex h-full min-h-[20rem] w-full max-w-4xl items-center justify-center"
					aria-label="AI agent status visualization"
				>
					<AgentVisualizer state={visualizerState} size="lg" />
					{remoteUsers.map((user) => (
						<div key={user.uid} className="hidden">
							<RemoteUser user={user} />
						</div>
					))}
				</section>
			}
			controls={
				<div className="jarvis-interaction-dock mx-auto flex w-full flex-col items-center gap-3 px-3">
					<TextMessageComposer
						value={messageDraft}
						isSending={isTextSending}
						isReady={isTextMessagingReady && isAgentConnected && connectionState === "CONNECTED"}
						error={textSendError}
						onChange={handleTextChange}
						onSend={handleSendText}
					/>
					<fieldset
						className="flex w-fit items-center gap-3 rounded-full border border-primary/20 bg-[#06141c]/90 px-4 py-2 shadow-[0_0_36px_rgba(50,209,220,0.08)] backdrop-blur-md"
						aria-label="Audio controls"
					>
						<div className="conversation-mic-host flex items-center justify-center">
							<MicButtonWithVisualizer
								isEnabled={isEnabled}
								setIsEnabled={setIsEnabled}
								track={localMicrophoneTrack}
								onToggle={handleMicToggle}
								className="overflow-visible"
								aria-label={isEnabled ? "Mute microphone" : "Unmute microphone"}
								enabledColor="hsl(var(--primary))"
								disabledColor="hsl(var(--destructive))"
							/>
						</div>
						<MicrophoneSelector localMicrophoneTrack={localMicrophoneTrack} />
					</fieldset>
				</div>
			}
			onEndConversation={handleEndConversation}
		/>
	);
}
