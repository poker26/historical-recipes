"""Temporal integration: durable orchestration of the book-processing pipeline.

The wizard's long-running steps (extract → cleanup → translate → analyze →
extract-recipes → match-ingredients → index) used to run as in-memory
``asyncio.create_task`` background jobs tracked by a process-local dict.  That
model loses all state if the backend restarts mid-run and gives no retry or
visibility.  This package re-implements the same steps as Temporal activities
orchestrated by ``BookPipelineWorkflow`` so a full book run survives restarts,
retries transient LLM/HTTP failures, and is observable in the Temporal UI.
"""
