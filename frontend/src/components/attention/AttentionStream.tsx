import { attentionCards } from "../../features/attention/state";
import { attentionT } from "../../features/attention/copy";
import { AttentionCard } from "./AttentionCard";

export function AttentionStream() {
  const cards = attentionCards.value;
  if (!cards.length) return null;
  return (
    <>
      <div class="col-title">
        <span class="attn-title-mark" aria-hidden="true" />
        <span>{attentionT("attention.title")}</span>
        <span class="attn-count">{cards.length}</span>
      </div>
      <div class="attn-list">
        {cards.map((card) => (
          <AttentionCard key={card.id} card={card} />
        ))}
      </div>
    </>
  );
}
