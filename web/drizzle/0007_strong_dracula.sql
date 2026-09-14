CREATE TYPE "public"."upload_mode" AS ENUM('model', 'estimate');--> statement-breakpoint
ALTER TABLE "model_versions" ADD COLUMN "mode" "upload_mode" DEFAULT 'model' NOT NULL;--> statement-breakpoint
-- Rows written before the column existed carry the mode their format implies.
-- Defaulting every one of them to 'model' would label the drawings already in
-- the catalogue as parts somebody modelled, which is the single thing this
-- column exists to prevent. The list is the estimate-mode extensions from
-- lib/formats.ts, without their dots: source_format stores them that way.
UPDATE "model_versions"
SET "mode" = 'estimate'
WHERE lower("source_format") IN (
  'dxf', 'pdf', 'png', 'jpg', 'jpeg', 'tif', 'tiff', 'bmp', 'webp'
);
