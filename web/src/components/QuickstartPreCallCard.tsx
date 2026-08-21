"use client";

import { ArrowRight, Database, Loader2, Radio, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";

type QuickstartPreCallCardProps = {
	isLoading: boolean;
	error: string | null;
	onStartConversation: () => void;
};

export function QuickstartPreCallCard({
	isLoading,
	error,
	onStartConversation,
}: QuickstartPreCallCardProps) {
	return (
		<div className="jarvis-launch-grid mx-auto w-[min(94vw,68rem)] animate-fade-up text-left">
			<div className="jarvis-launch-copy">
				<div className="jarvis-eyebrow"><span /> Agora Conversational AI demo</div>
				<p className="jarvis-serial">MARK // 01 · TASK OPERATIONS</p>
				<h1>Voice in.<br /><em>Work logged.</em></h1>
				<p className="jarvis-intro">
					A real-time voice assistant that turns a spoken instruction into a live Notion task—then confirms it out loud.
				</p>

				<div className="jarvis-example">
					<span>Try saying</span>
					<strong>“Add prepare the KOL demo to my Notion tasks.”</strong>
				</div>

				<Button
					onClick={onStartConversation}
					disabled={isLoading}
					className="jarvis-launch-button mt-9 h-12 rounded-none px-6 text-sm font-semibold uppercase tracking-[0.14em]"
				aria-label={
					isLoading
						? "Starting conversation with AI agent"
						: "Start conversation with AI agent"
				}
				>
					{isLoading ? <><Loader2 className="h-4 w-4 animate-spin" /> Initializing</> : <>Start voice link <ArrowRight className="h-4 w-4" /></>}
				</Button>
				{error ? <p className="mt-3 text-xs text-destructive">{error}</p> : null}
			</div>

			<div className="jarvis-core" aria-hidden="true">
				<div className="jarvis-core-ring ring-a" />
				<div className="jarvis-core-ring ring-b" />
				<div className="jarvis-core-ring ring-c" />
				<div className="jarvis-core-center"><span>J</span><small>ONLINE</small></div>
			</div>

			<div className="jarvis-capabilities">
				<div><Radio /><span><strong>Real-time voice</strong><small>Agora RTC + RTM</small></span></div>
				<div><Sparkles /><span><strong>Agent reasoning</strong><small>OpenAI tool calling</small></span></div>
				<div><Database /><span><strong>Live execution</strong><small>Notion REST API</small></span></div>
			</div>
		</div>
	);
}
