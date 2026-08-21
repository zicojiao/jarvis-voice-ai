"use client";

import { ArrowUpRight, Check, Database, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { getRecentTasks, type RecentTask } from "@/services/api";

export function RecentTasksPanel() {
	const [tasks, setTasks] = useState<RecentTask[]>([]);
	const [configured, setConfigured] = useState<boolean | null>(null);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		let cancelled = false;
		const refresh = async () => {
			try {
				const result = await getRecentTasks();
				if (!cancelled) {
					setTasks(result.tasks);
					setConfigured(result.configured);
					setError(null);
				}
			} catch (nextError) {
				if (!cancelled) {
					setError(nextError instanceof Error ? nextError.message : "Task feed unavailable");
				}
			}
		};
		void refresh();
		const timer = setInterval(refresh, 1800);
		return () => {
			cancelled = true;
			clearInterval(timer);
		};
	}, []);

	return (
		<section className="jarvis-panel flex min-h-0 flex-1 flex-col" aria-label="Notion task activity">
			<div className="jarvis-panel-header">
				<div>
					<p className="jarvis-kicker">External system</p>
					<h2>Notion task log</h2>
				</div>
				<Database className="h-4 w-4 text-primary" aria-hidden="true" />
			</div>

			<div className="min-h-0 flex-1 overflow-y-auto p-3">
				{error ? <p className="jarvis-empty text-destructive">{error}</p> : null}
				{configured === false ? (
					<div className="jarvis-empty">
						<span>Notion link pending</span>
						<small>Add the API key and data source ID on the backend.</small>
					</div>
				) : configured === null ? (
					<div className="jarvis-empty flex-row gap-2">
						<LoaderCircle className="h-4 w-4 animate-spin" />
						<span>Reading task channel</span>
					</div>
				) : tasks.length === 0 ? (
					<div className="jarvis-empty">
						<span>No tasks captured</span>
						<small>Say “Add prepare the KOL demo to my Notion tasks.”</small>
					</div>
				) : (
					<div className="space-y-2">
						{tasks.map((task) => (
							<a
								key={task.id}
								href={task.url}
								target="_blank"
								rel="noreferrer"
								className="jarvis-task group"
							>
								<span className="jarvis-task-check"><Check className="h-3 w-3" /></span>
								<span className="min-w-0 flex-1">
									<strong>{task.title}</strong>
									<small>{task.priority ? `${task.priority} priority` : "Created in Notion"}</small>
								</span>
								<ArrowUpRight className="h-3.5 w-3.5 text-muted-foreground transition group-hover:text-primary" />
							</a>
						))}
					</div>
				)}
			</div>
		</section>
	);
}
