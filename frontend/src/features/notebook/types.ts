/** Notebook cell record. Shape matches app.js live/persisted cells. */
export type NotebookCell = {
  producing_cell_id?: string;
  cell_id?: string;
  cell_index?: number | string | null;
  kernel_id?: string;
  language?: string;
  origin?: string | null;
  source?: string;
  stdout?: string;
  stderr?: string;
  error?: string;
  status?: string;
  figures?: string[];
  files_written?: string[];
  files_read?: string[];
  complete?: boolean;
  draft?: boolean;
  live?: boolean;
  _draftRevision?: number;
  _seenChunks?: Record<string, boolean>;
  generation_id?: string;
  state_revision?: number | string | null;
  attempt_group_id?: string;
  revision_of?: string | null;
  attempt?: number;
  attempt_count?: number;
  is_latest_attempt?: boolean;
  replay_policy?: string;
  visibility?: string;
  stale?: boolean;
  stale_reasons?: unknown[];
  fork_checkpoint_id?: string;
  environment?: string;
  env?: string;
  _out?: boolean;
  _revisions?: NotebookCell[];
  _historicalRevision?: boolean;
};

export type KernelStatus = {
  state?: string;
  alive?: boolean;
  turn_running?: boolean;
  generation?: number | string;
  generation_id?: string;
  python_generation_id?: string;
  env?: {
    name?: string;
    kernel_id?: string;
    python_version?: string;
    pending?: string;
  };
  view_only?: boolean;
  trust_state?: string;
  repl_enabled?: boolean;
  artifact_workbench?: boolean;
  explicit_recovery_required?: boolean;
};

export type KernelEnvRow = {
  name: string;
  runnable?: boolean;
  notable?: string[];
  description?: string;
};

export type ScrollBox = {
  scrollHeight: number;
  scrollTop: number;
  clientHeight: number;
  _nbScrollBound?: boolean;
  addEventListener?: (
    type: string,
    listener: () => void,
    opts?: { passive?: boolean },
  ) => void;
};

export type NotebookApi = (
  path: string,
  init?: RequestInit,
) => Promise<Record<string, unknown> | null>;

export type WindowTarget = Record<string, unknown>;
