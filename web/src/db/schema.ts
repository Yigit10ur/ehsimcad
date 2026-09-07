/**
 * Database schema.
 *
 * Mirrors ARCHITECTURE.md section 4. The shape to keep in mind: a `model` is
 * the thing people talk about, a `model_version` is the thing that actually
 * gets converted and viewed. Every version keeps its own derived files
 * permanently so past revisions stay openable.
 */

import { relations, sql } from 'drizzle-orm';
import {
  bigint,
  doublePrecision,
  index,
  integer,
  jsonb,
  pgEnum,
  pgTable,
  primaryKey,
  text,
  timestamp,
  uniqueIndex,
  uuid,
} from 'drizzle-orm/pg-core';
import type { AdapterAccountType } from 'next-auth/adapters';

export const visibilityEnum = pgEnum('visibility', ['private', 'public']);
export const memberRoleEnum = pgEnum('member_role', ['owner', 'editor', 'viewer']);
export const emailTokenPurposeEnum = pgEnum('email_token_purpose', [
  'password_reset',
  'email_verification',
]);
export const conversionStatusEnum = pgEnum('conversion_status', [
  // A version exists before its file does: the row is created first so the
  // upload has a key to write to. Only once the browser reports the upload
  // finished does it become 'queued' and visible to the worker, so a
  // half-uploaded file is never handed to the converter.
  'uploading',
  'queued',
  'processing',
  'ready',
  'failed',
]);

export const users = pgTable('users', {
  id: uuid('id').primaryKey().defaultRandom(),
  email: text('email').notNull().unique(),
  /**
   * Null for an account that signs in through GitHub, which is most of them.
   * The format carries its own scrypt parameters -- see `lib/password.ts`.
   */
  passwordHash: text('password_hash'),
  emailVerified: timestamp('email_verified', { withTimezone: true }),
  name: text('name'),
  image: text('image'),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
});

/**
 * Auth.js tables.
 *
 * Shapes are dictated by the Drizzle adapter, not by us -- the column names
 * and the composite keys are what it looks for.
 */

export const accounts = pgTable(
  'accounts',
  {
    userId: uuid('user_id')
      .notNull()
      .references(() => users.id, { onDelete: 'cascade' }),
    type: text('type').$type<AdapterAccountType>().notNull(),
    provider: text('provider').notNull(),
    providerAccountId: text('provider_account_id').notNull(),
    refresh_token: text('refresh_token'),
    access_token: text('access_token'),
    expires_at: integer('expires_at'),
    token_type: text('token_type'),
    scope: text('scope'),
    id_token: text('id_token'),
    session_state: text('session_state'),
  },
  (table) => [
    primaryKey({ columns: [table.provider, table.providerAccountId] }),
  ],
);

export const sessions = pgTable('sessions', {
  sessionToken: text('session_token').primaryKey(),
  userId: uuid('user_id')
    .notNull()
    .references(() => users.id, { onDelete: 'cascade' }),
  expires: timestamp('expires', { withTimezone: true }).notNull(),
});

export const verificationTokens = pgTable(
  'verification_tokens',
  {
    identifier: text('identifier').notNull(),
    token: text('token').notNull(),
    expires: timestamp('expires', { withTimezone: true }).notNull(),
  },
  (table) => [primaryKey({ columns: [table.identifier, table.token] })],
);

export const projects = pgTable(
  'projects',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    ownerId: uuid('owner_id')
      .notNull()
      .references(() => users.id, { onDelete: 'cascade' }),
    name: text('name').notNull(),
    slug: text('slug').notNull(),
    description: text('description'),
    visibility: visibilityEnum('visibility').notNull().default('private'),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [uniqueIndex('projects_owner_slug_idx').on(table.ownerId, table.slug)],
);

export const models = pgTable(
  'models',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    projectId: uuid('project_id')
      .notNull()
      .references(() => projects.id, { onDelete: 'cascade' }),
    name: text('name').notNull(),
    description: text('description'),
    // Deliberately not a foreign key to model_versions: the two tables would
    // reference each other and neither row could be inserted first.
    currentVersionId: uuid('current_version_id'),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [index('models_project_idx').on(table.projectId)],
);

export const modelVersions = pgTable(
  'model_versions',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    modelId: uuid('model_id')
      .notNull()
      .references(() => models.id, { onDelete: 'cascade' }),
    versionNo: bigint('version_no', { mode: 'number' }).notNull(),

    sourceKey: text('source_key').notNull(),
    /**
     * The name of the file as uploaded.
     *
     * `sourceKey` normalises to `source.step`, and the catalogue name may be
     * replaced by the one the CAD file declares, so without this column the
     * name the user actually chose survives nowhere. Null on versions uploaded
     * before the column existed.
     */
    sourceFilename: text('source_filename'),
    sourceFormat: text('source_format').notNull(),
    sourceSizeBytes: bigint('source_size_bytes', { mode: 'number' }).notNull(),
    /**
     * How long the part is along its own axis, in millimetres, when the file
     * does not say.
     *
     * A printed sheet carries the part at whatever scale it was plotted and
     * nothing in it records which, so one known length is what turns a shape
     * into a size. Null everywhere else, and on a sheet whose uploader did not
     * know -- there the sheet is taken as printed full size and the model says
     * so.
     */
    sourceLengthMm: doublePrecision('source_length_mm'),

    glbKey: text('glb_key'),
    metadataKey: text('metadata_key'),
    thumbnailKey: text('thumbnail_key'),

    status: conversionStatusEnum('status').notNull().default('uploading'),
    errorMessage: text('error_message'),
    /** Triangle count, deflection, part count -- filled in by the converter. */
    stats: jsonb('stats'),

    /**
     * Set when a worker claims the row and cleared when it finishes. A row
     * left `processing` with an old timestamp is a crashed job, not a running
     * one, which is what lets the queue recover without a scheduler.
     */
    claimedAt: timestamp('claimed_at', { withTimezone: true }),

    createdBy: uuid('created_by').references(() => users.id, { onDelete: 'set null' }),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [
    uniqueIndex('model_versions_model_no_idx').on(table.modelId, table.versionNo),
    index('model_versions_queue_idx')
      .on(table.status, table.createdAt)
      .where(sql`${table.status} in ('queued', 'processing')`),
  ],
);

export const projectMembers = pgTable(
  'project_members',
  {
    projectId: uuid('project_id')
      .notNull()
      .references(() => projects.id, { onDelete: 'cascade' }),
    userId: uuid('user_id')
      .notNull()
      .references(() => users.id, { onDelete: 'cascade' }),
    role: memberRoleEnum('role').notNull().default('viewer'),
  },
  (table) => [uniqueIndex('project_members_pk').on(table.projectId, table.userId)],
);

/**
 * A one-time link sent to an address, for resetting a password or proving that
 * the address belongs to whoever is reading it.
 *
 * The token is stored hashed, exactly like a password. A reset link is a
 * password for as long as it lives, and a database that can be read must not
 * hand out working links. The row is deleted when it is used, so a link works
 * once.
 */
export const emailTokens = pgTable(
  'email_tokens',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    /** The address the link was sent to, lowercased. */
    email: text('email').notNull(),
    purpose: emailTokenPurposeEnum('purpose').notNull(),
    /** SHA-256 of the token in the link. The link itself is never stored. */
    tokenHash: text('token_hash').notNull().unique(),
    expiresAt: timestamp('expires_at', { withTimezone: true }).notNull(),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [index('email_tokens_email_idx').on(table.email, table.purpose)],
);

/**
 * One failed sign-in, kept just long enough to slow down guessing.
 *
 * Keyed by the address rather than by the account, because the addresses that
 * do not exist are exactly the ones an attacker is enumerating, and they have
 * no account to hang a counter on. A serverless host has no memory between
 * requests to count in, so the counter has to live here.
 */
export const signInAttempts = pgTable(
  'sign_in_attempts',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    email: text('email').notNull(),
    at: timestamp('at', { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [index('sign_in_attempts_idx').on(table.email, table.at)],
);

/**
 * Someone invited to a project who has not signed in yet.
 *
 * A person only becomes a row in `users` the first time they authenticate, so
 * without this the only way to share is "sign in first and then I will add
 * you". The invitation waits here and is turned into a membership when they
 * arrive. Nothing is emailed: telling them is still a conversation between two
 * people, which is what it would be anyway.
 */
export const projectInvitations = pgTable(
  'project_invitations',
  {
    id: uuid('id').primaryKey().defaultRandom(),
    projectId: uuid('project_id')
      .notNull()
      .references(() => projects.id, { onDelete: 'cascade' }),
    /** Stored lowercased, because that is how it will be matched. */
    email: text('email').notNull(),
    role: memberRoleEnum('role').notNull().default('viewer'),
    invitedBy: uuid('invited_by').references(() => users.id, { onDelete: 'set null' }),
    createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [uniqueIndex('project_invitations_idx').on(table.projectId, table.email)],
);

export const projectsRelations = relations(projects, ({ one, many }) => ({
  owner: one(users, { fields: [projects.ownerId], references: [users.id] }),
  models: many(models),
}));

export const modelsRelations = relations(models, ({ one, many }) => ({
  project: one(projects, { fields: [models.projectId], references: [projects.id] }),
  versions: many(modelVersions),
}));

export const modelVersionsRelations = relations(modelVersions, ({ one }) => ({
  model: one(models, { fields: [modelVersions.modelId], references: [models.id] }),
}));

export type Model = typeof models.$inferSelect;
export type ProjectMember = typeof projectMembers.$inferSelect;
export type ProjectInvitation = typeof projectInvitations.$inferSelect;
export type ModelVersion = typeof modelVersions.$inferSelect;
export type Project = typeof projects.$inferSelect;
