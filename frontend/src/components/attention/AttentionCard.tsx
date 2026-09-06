import type { AttentionCardModel } from "../../features/attention/types";
import { applyNavigation } from "../../features/attention/navigate";

type Props = { card: AttentionCardModel };

export function AttentionCard({ card }: Props) {
  const open = () => {
    void applyNavigation(card.navigation);
  };
  const hintBase = card.actionHint.split(":")[0] || card.actionHint;

  return (
    <button
      type="button"
      class={`attn-card severity-${card.severity}`}
      data-kind={card.sourceKind}
      data-state={card.state}
      data-dock={card.navigation.dock}
      data-frame-id={card.frameId}
      data-surface={card.navigation.surface}
      onClick={open}
    >
      <div class="attn-body">
        <div class="attn-title">{card.title}</div>
        <div class="attn-sub">
          {card.projectName ? (
            <span class="attn-project">{card.projectName}</span>
          ) : null}
          {card.projectName ? <span class="attn-dot">·</span> : null}
          <span class="attn-kind">{card.kindLabel}</span>
        </div>
      </div>
      <div class="attn-foot">
        <span class={`attn-hint hint-${hintBase}`}>
          {card.actionLabel}
        </span>
        {card.updatedLabel ? <span class="attn-when">{card.updatedLabel}</span> : null}
      </div>
    </button>
  );
}
