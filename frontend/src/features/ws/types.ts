/** Loose WS JSON object. Every field is optional; the server is not uniform. */
export type WsMessage = {
  type?: string;
  root_frame_id?: string;
  frame_id?: string;
  seq?: number;
  epoch?: string;
  gap?: unknown;
  status?: string;
  execution_id?: string;
  request_id?: string;
  artifact?: Record<string, unknown>;
  artifact_id?: string;
  filename?: string;
  task_summary?: string;
  name?: string;
  producing_cell_id?: string;
  [key: string]: unknown;
};

export type WsHandler = (m: WsMessage) => void;
