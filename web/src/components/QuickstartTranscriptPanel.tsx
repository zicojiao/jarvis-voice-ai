"use client";

import { useEffect, useMemo, useRef } from "react";

type TranscriptMessage = {
	turn_id?: string | number;
	uid: number;
	text?: string;
	createdAt?: number;
};

type QuickstartTranscriptPanelProps = {
	messageList: TranscriptMessage[];
	currentInProgressMessage: TranscriptMessage | null;
	agentUID: string;
};

function formatMessageTime(createdAt?: number) {
	if (!createdAt) return null;
	return new Intl.DateTimeFormat(undefined, {
		hour: "numeric",
		minute: "2-digit",
	}).format(new Date(createdAt));
}

export function QuickstartTranscriptPanel({
	messageList,
	currentInProgressMessage,
	agentUID,
}: QuickstartTranscriptPanelProps) {
	const scrollRef = useRef<HTMLDivElement>(null);
	const messages = useMemo(
		() =>
			currentInProgressMessage
				? [...messageList, currentInProgressMessage]
				: messageList,
		[currentInProgressMessage, messageList],
	);

	useEffect(() => {
		const node = scrollRef.current;
		if (!node) return;
		node.scrollTop = node.scrollHeight;
	});

	return (
		<section
			className="jarvis-panel flex h-full min-h-0 w-full flex-col overflow-hidden"
			aria-label="Transcription panel"
		>
			<div className="jarvis-panel-header">
				<div>
					<p className="jarvis-kicker">Audio channel</p>
					<h2>Live transcript</h2>
				</div>
			</div>

			<div
				ref={scrollRef}
				className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-4"
			>
				{messages.length === 0 ? (
					<div className="jarvis-empty h-full">
						Start speaking to see the live transcript here.
					</div>
				) : (
					messages.map((message, index) => {
						const isAgent = String(message.uid) === agentUID;
						const label = isAgent ? "J.A.R.V.I.S." : "Operator";
						const text = message.text?.trim();
						const time = formatMessageTime(message.createdAt);

						return (
							<article
								key={`${message.turn_id ?? message.uid}-${index}`}
								className={`flex flex-col ${isAgent ? "items-start" : "items-end"}`}
							>
								<div className="mb-1 flex items-center gap-2 px-1 text-xs font-semibold text-muted-foreground">
									<span>{label}</span>
									{time ? <span className="font-normal">{time}</span> : null}
								</div>
								<div
									className={`max-w-full whitespace-pre-wrap border px-3 py-2 text-sm leading-6 ${
										isAgent
											? "border-primary/20 bg-primary/5 text-[#d9f7f8]"
											: "border-white/10 bg-white/[0.035] text-[#d7e0e5]"
									}`}
								>
									{text || "..."}
								</div>
							</article>
						);
					})
				)}
			</div>
		</section>
	);
}
