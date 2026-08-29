import type { ComponentChildren } from "preact";
import { Icon } from "./icons";

export function Hdr({ title, sub }: { title: string; sub: string }) {
  return (
    <div>
      <div class="cust-h">{title}</div>
      <div class="cust-sub">{sub}</div>
    </div>
  );
}

export function CustRow({
  name,
  desc,
  children,
  class: cls,
}: {
  name?: string;
  desc?: ComponentChildren;
  children?: ComponentChildren;
  class?: string;
}) {
  return (
    <div class={"cust-row" + (cls ? " " + cls : "")}>
      <div class="info">
        {name != null ? <div class="nm">{name}</div> : null}
        {desc != null ? <div class="ds">{desc}</div> : null}
      </div>
      {children}
    </div>
  );
}

export function InfoRow({ name, detail }: { name: string; detail: string }) {
  return (
    <div class="cust-row">
      <div class="info">
        <div class="nm">{name}</div>
        <div class="ds">{detail}</div>
      </div>
    </div>
  );
}

export function Seg({
  value,
  options,
  onPick,
}: {
  value: string;
  options: Array<[string, string]>;
  onPick: (value: string) => void;
}) {
  return (
    <div class="seg">
      {options.map(([val, label]) => (
        <button
          key={val}
          type="button"
          class={"seg-btn" + (val === value ? " active" : "")}
          onClick={() => onPick(val)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({
  on,
  disabled,
  title,
  onClick,
}: {
  on: boolean;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      class={"toggle" + (on ? " on" : "") + (disabled ? " off" : "")}
      disabled={disabled}
      title={title}
      onClick={onClick}
    />
  );
}

export function Pill({ children }: { children: ComponentChildren }) {
  return <span class="pill">{children}</span>;
}

export function IconGhost({
  name,
  title,
  onClick,
  size = 15,
}: {
  name: string;
  title?: string;
  onClick: () => void;
  size?: number;
}) {
  return (
    <button type="button" class="icon-ghost" title={title} onClick={onClick}>
      <Icon name={name} size={size} />
    </button>
  );
}

export function Note({ children }: { children: ComponentChildren }) {
  return <div class="cust-note">{children}</div>;
}

export function Subhead({ children }: { children: ComponentChildren }) {
  return <div class="cust-subhead">{children}</div>;
}

export function Empty({ children }: { children: ComponentChildren }) {
  return <div class="dock-empty">{children}</div>;
}

export function LoadErr({ message }: { message: string }) {
  return <div class="cust-note">{message}</div>;
}
