"use client";

export type QuickstartAgentMetric = {
	type: string;
	name: string;
	value: number;
	timestamp: number;
};

type QuickstartPipelineMetricsProps = {
	metrics: QuickstartAgentMetric[];
};

const PIPELINE = [
	{ key: "stt", label: "Deepgram STT", metricTypes: ["stt", "asr"] },
	{ key: "llm", label: "Tool LLM", metricTypes: ["llm", "mllm"] },
	{ key: "tts", label: "MiniMax TTS", metricTypes: ["tts"] },
] as const;

function formatMetricName(name: string) {
	return name.replace(/[_-]+/g, " ");
}

export function QuickstartPipelineMetrics({
	metrics,
}: QuickstartPipelineMetricsProps) {
	const latestByType = new Map<string, QuickstartAgentMetric>();
	for (const metric of metrics) {
		latestByType.set(metric.type.toLowerCase(), metric);
	}

	return (
		<div className="flex min-w-0 flex-wrap items-center gap-2">
			<span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
				Live pipeline
			</span>
			{PIPELINE.map((step, index) => {
				const metric = step.metricTypes
					.map((type) => latestByType.get(type))
					.find(Boolean);

				return (
					<div key={step.key} className="flex items-center gap-2">
						{index > 0 ? (
							<span
								className="text-xs text-muted-foreground"
								aria-hidden="true"
							>
								/
							</span>
						) : null}
						<span className="border border-primary/15 bg-primary/[0.035] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-foreground">
							{step.label}
							{metric ? (
								<span
									className="ml-2 text-primary"
									title={new Date(metric.timestamp).toLocaleTimeString()}
								>
									{formatMetricName(metric.name)} {Math.round(metric.value)}ms
								</span>
							) : null}
						</span>
					</div>
				);
			})}
		</div>
	);
}
