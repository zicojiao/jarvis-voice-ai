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
				<div className="jarvis-eyebrow"><span /> JARVIS voice interface</div>
				<p className="jarvis-serial">AGORA RTC · MANAGED VOICE · CONNECTED TOOLS</p>
				<h1>Ask anything.<br /><em>Take action.</em></h1>
				<p className="jarvis-intro">
					Talk to JARVIS naturally. Ask a question, work through an idea, or have it take action with connected tools when needed.
				</p>

				<Button
					onClick={onStartConversation}
					disabled={isLoading}
					className="jarvis-launch-button mt-10 h-12 rounded-none px-6 text-sm font-semibold uppercase tracking-[0.14em]"
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
				<div><Sparkles /><span><strong>AI assistant</strong><small>Fast, conversational reasoning</small></span></div>
				<div><Database /><span><strong>Connected tools</strong><small>Actions available on demand</small></span></div>
			</div>
		</div>
	);
}
