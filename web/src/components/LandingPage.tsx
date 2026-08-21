"use client";

import type { RTMClient } from "agora-rtm";
import dynamic from "next/dynamic";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { ErrorBoundary } from "@/components/ErrorBoundary";
import { LoadingSkeleton } from "@/components/LoadingSkeleton";
import { QuickstartPreCallCard } from "@/components/QuickstartPreCallCard";
import { getConfig, startAgent, stopAgent } from "@/services/api";
import type { AgoraRenewalTokens, AgoraTokenData } from "@/types/conversation";

// 会话主体依赖浏览器媒体设备和 Agora Web SDK，因此禁用服务端渲染，
// 避免服务端执行阶段访问 window、WebRTC 或 MediaDevices 等浏览器 API。
const ConversationComponent = dynamic(
	() => import("@/components/ConversationComponent"),
	{
		ssr: false,
	},
);

// RTM login() 完成后，底层连接状态仍可能处于 CONNECTING。
// 此辅助函数等待 CONNECTED 事件，并设置有限等待时间，防止初始化流程无限阻塞。
function waitForRtmConnected(rtmClient: RTMClient, timeoutMs = 600): Promise<void> {
	return new Promise((resolve) => {
		// settled 保证状态事件与超时回调之间只有一个分支能够完成 Promise。
		let settled = false;
		let timer: ReturnType<typeof setTimeout> | null = null;

		// 统一处理监听器注销、定时器清理和 Promise 完成，避免残留事件订阅。
		const finish = () => {
			if (settled) return;
			settled = true;
			if (timer) clearTimeout(timer);
			rtmClient.removeEventListener("status", onStatus);
			resolve();
		};

		// 不同 RTM SDK 版本可能使用 newState 或 state 表示目标状态，
		// 因此在运行时兼容两种事件数据结构。
		const onStatus = (
			connectionStatus:
				| { newState?: string }
				| { state?: string }
				| Record<string, unknown>,
		) => {
			const nextState =
				typeof connectionStatus === "object" && connectionStatus !== null
					? "newState" in connectionStatus
						? connectionStatus.newState
						: "state" in connectionStatus
							? connectionStatus.state
							: undefined
					: undefined;
			if (nextState === "CONNECTED") {
				finish();
			}
		};

		// 先注册监听器再设置超时，确保连接状态变化不会在监听窗口之外丢失。
		rtmClient.addEventListener("status", onStatus);
		timer = setTimeout(finish, timeoutMs);
	});
}

// AgoraRTCProvider 必须运行在客户端环境中；动态导入同时缩小首屏服务端模块图。
const AgoraProvider = dynamic(
	async () => {
		const { AgoraRTCProvider, default: AgoraRTC } = await import(
			"agora-rtc-react"
		);

		return {
			default: function AgoraProviders({
				children,
			}: { children: React.ReactNode }) {
				// RTC Client 在组件生命周期内必须保持引用稳定。
				// useRef 可避免 React Strict Mode 重渲染时重复创建多个 RTC Client。
				const clientRef = useRef<ReturnType<
					typeof AgoraRTC.createClient
				> | null>(null);
				if (!clientRef.current) {
					clientRef.current = AgoraRTC.createClient({
						mode: "rtc",
						codec: "vp8",
					});
				}
				return (
					<AgoraRTCProvider client={clientRef.current}>
						{children}
					</AgoraRTCProvider>
				);
			},
		};
	},
	{ ssr: false },
);

export default function LandingPage() {
	// 会话页面状态：控制预呼叫页面与实时会话页面之间的切换。
	const [showConversation, setShowConversation] = useState(false);
	// 后端签发的 RTC/RTM 连接参数只保存在当前浏览器会话内存中。
	const [agoraData, setAgoraData] = useState<AgoraTokenData | null>(null);
	// RTM Client 由父组件持有，以便初始化、续期和退出使用同一实例。
	const [rtmClient, setRtmClient] = useState<RTMClient | null>(null);
	// 启动流程状态用于禁止重复请求，并向预呼叫界面反馈执行进度。
	const [isLoading, setIsLoading] = useState(false);
	const [error, setError] = useState<string | null>(null);
	useEffect(() => {
		// 空闲阶段预加载 RTC 与 RTM 代码，以降低用户点击启动后的模块下载延迟。
		// 预加载失败不阻断页面渲染；正式初始化时仍会返回可见错误。
		import("agora-rtc-react").catch(() => {});
		import("agora-rtm").catch(() => {});
	}, []);

	const handleStartConversation = async () => {
		// 每次启动前重置上一次会话遗留的错误状态。
		setIsLoading(true);
		setError(null);
		let startedAgentId: string | undefined;
		let nextRtm: RTMClient | null = null;

		try {
			// 从 Python 后端获取本次会话的短期鉴权配置。
			// 返回值包含 RTC/RTM Token、唯一频道标识、用户 UID 与 Agent UID。
			const config = await getConfig();
			const appId = config.app_id;

			// 先确认云端 Agent 已成功创建，避免 Agent 启动失败时进入不可用的会话页。
			startedAgentId = await startAgent(
				config.channel_name,
				Number(config.agent_uid),
				Number(config.uid),
			);

			// RTM 使用与 Token subject 一致的用户 UID 登录，并订阅当前会话频道。
			const { default: AgoraRTM } = await import("agora-rtm");
			nextRtm = new AgoraRTM.RTM(appId, config.uid);
			await nextRtm.login({ token: config.token });
			await waitForRtmConnected(nextRtm);
			await nextRtm.subscribe(config.channel_name);

			// 只有 RTM 登录和频道订阅完成后才切换至会话界面，
			// 确保子组件挂载时能够直接注册 RTM 事件和消息订阅。
			setRtmClient(nextRtm);
			setAgoraData({
				token: config.token,
				uid: config.uid,
				channel: config.channel_name,
				appId: config.app_id,
				agentUid: config.agent_uid,
				agentId: startedAgentId,
			});
			setShowConversation(true);
		} catch (nextError) {
			// 任一初始化步骤失败都回收已经创建的资源，并停留在可重试的启动页。
			if (nextRtm) {
				await nextRtm.logout().catch(() => {});
			}
			if (startedAgentId) {
				await stopAgent(startedAgentId).catch(() => {});
			}
			setError(
				nextError instanceof Error && nextError.message
					? nextError.message
					: "Failed to start conversation. Please try again.",
			);
			console.error("Error starting conversation:", nextError);
		} finally {
			// 无论成功或失败都解除启动按钮的加载状态。
			setIsLoading(false);
		}
	};

	const handleTokenWillExpire = useCallback(
		async (uid: string): Promise<AgoraRenewalTokens> => {
			try {
				// 续期必须沿用原频道，否则新 Token 无法授权当前 RTC/RTM 连接。
				const channel = agoraData?.channel;
				if (!channel) {
					throw new Error("Missing channel for token renewal");
				}

				// RTC Token 使用 SDK 实际加入频道的 UID；RTM Token 使用 RTM 登录时的 UID。
				// 两种 Token 并发签发，以避免顺序请求增加续期窗口内的连接风险。
				const [rtcConfig, rtmConfig] = await Promise.all([
					getConfig({ channel, uid }),
					getConfig({ channel, uid: agoraData.uid }),
				]);

				return {
					rtcToken: rtcConfig.token,
					rtmToken: rtmConfig.token,
				};
			} catch (error) {
				console.error("Error renewing token:", error);
				throw error;
			}
		},
		[agoraData],
	);

	const handleEndConversation = async () => {
		// 优先停止云端 Agent，避免客户端退出后 Agent 继续占用会话并产生服务费用。
		if (agoraData?.agentId) {
			try {
				await stopAgent(agoraData.agentId);
			} catch (nextError) {
				console.error("Failed to stop agent:", nextError);
			}
		}

		// RTM 退出失败不阻止本地 UI 状态复位；错误仅记录到控制台用于诊断。
		rtmClient?.logout().catch((err) => console.error("RTM logout error:", err));
		// 清除连接对象和鉴权数据，使下一次启动创建完全独立的新会话。
		setRtmClient(null);
		setAgoraData(null);
		setShowConversation(false);
	};

	return (
		<div className="jarvis-app relative flex h-dvh min-h-screen flex-col overflow-hidden bg-background text-foreground">
			<div
				className={`flex min-h-0 flex-1 flex-col ${
					showConversation
						? "items-stretch justify-start"
						: "items-center justify-center"
				}`}
			>
				<div
					className={`z-10 flex min-h-0 flex-1 flex-col ${
						showConversation
							? "h-full w-full max-w-none items-stretch gap-0 px-0 text-left"
							: "w-full max-w-none items-center justify-center px-4 text-center"
					}`}
				>
					{!showConversation ? (
						<QuickstartPreCallCard
							isLoading={isLoading}
							error={error}
							onStartConversation={handleStartConversation}
						/>
					) : agoraData && rtmClient ? (
						<>
							<Suspense fallback={<LoadingSkeleton />}>
								<ErrorBoundary>
									<AgoraProvider>
										<ConversationComponent
											agoraData={agoraData}
											rtmClient={rtmClient}
											onTokenWillExpire={handleTokenWillExpire}
											onEndConversation={handleEndConversation}
										/>
									</AgoraProvider>
								</ErrorBoundary>
							</Suspense>
						</>
					) : (
						<p className="text-sm text-muted-foreground">
							Failed to load conversation data.
						</p>
					)}
				</div>
			</div>

			{!showConversation ? (
				<footer className="jarvis-footer"><span>JARVIS PROTOTYPE</span><a href="https://agora.io/en/" target="_blank" rel="noreferrer">Powered by Agora</a></footer>
			) : null}
		</div>
	);
}
