/**
 * Same signal Proxy as window.S. Imported from stores/registry (not
 * window-exports.ts) so asking for S does not reinstall the export layer.
 */
import { createSProxy } from "../../stores/registry";

export type SBag = Record<string, any>;

export const S = createSProxy() as unknown as SBag;
