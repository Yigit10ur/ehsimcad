-- The new index first, the old one after it.
--
-- Generated the other way round, which would leave `models` with nothing on
-- `project_id` for as long as the create takes. Harmless on a catalogue this
-- size and still the wrong order to write down: the shape of this pair is what
-- someone copies the next time an index is replaced.
--
-- Not CONCURRENTLY: that cannot run inside a transaction, and migrations here
-- are one. A plain CREATE INDEX blocks writes to `models` while it builds,
-- which for a table of this size is shorter than the deploy around it.
CREATE INDEX "models_project_created_idx" ON "models" USING btree ("project_id","created_at" DESC NULLS LAST,"id" DESC NULLS LAST);--> statement-breakpoint
-- A strict prefix of the index above, so it can answer nothing that one
-- cannot, and every insert was paying to maintain both.
DROP INDEX "models_project_idx";
