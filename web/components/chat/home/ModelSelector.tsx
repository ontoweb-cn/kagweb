"use client";

import { useEffect, useRef, useState } from "react";
import { AlertCircle, Bot, Check, ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLingerExpand } from "@/hooks/use-linger-expand";
import ProviderIcon from "@/components/common/ProviderIcon";

/**
 * One row of the composer's model picker, normalized out of whichever source
 * drives it — the conversation catalog (LLMOption) or the agent backend's own
 * vocabulary (BackendModelOption). `key` is what `onChange` hands back.
 */
export interface ModelPickerOption {
  key: string;
  /** Primary label — the model id actually sent for the turn. */
  label: string;
  /** Trailing muted text (provider / profile / description). */
  detail?: string;
  /** Whole-row hover tooltip; defaults to the label. */
  title?: string;
  /** Small right-side text (context window). */
  trailing?: string;
  /** Badge between label and detail ("Default", "Current"). */
  badge?: string;
  /** Brand icon (catalog rows); backend rows may omit it. */
  provider?: string;
}

export function formatContextWindow(value?: number) {
  if (!value) return "";
  if (value >= 1_000_000) return `${Math.round(value / 1_000_000)}M ctx`;
  if (value >= 1_000) return `${Math.round(value / 1_000)}k ctx`;
  return `${value} ctx`;
}

function PickerOptionRow({
  option,
  selected,
  onSelect,
}: {
  option: ModelPickerOption;
  selected: boolean;
  onSelect: () => void;
}) {
  // The model id is the primary label (what gets sent for the turn); the
  // nickname/profile/description live in the tooltip. Long ids
  // ("google/gemini-3-flash-preview") get ellipsized by the inline layout;
  // hovering the row reveals the full id as an overlay. The scrollWidth
  // check at mouseenter time keeps the overlay away from rows that aren't
  // actually truncated.
  const nameRef = useRef<HTMLSpanElement>(null);
  const [revealFull, setRevealFull] = useState(false);
  return (
    <button
      type="button"
      title={option.title || option.label}
      onClick={onSelect}
      onMouseEnter={() => {
        const el = nameRef.current;
        setRevealFull(!!el && el.scrollWidth > el.clientWidth + 1);
      }}
      onMouseLeave={() => setRevealFull(false)}
      className={`relative flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors active:bg-[var(--muted)]/70 ${
        selected ? "bg-[var(--primary)]/[0.06]" : "hover:bg-[var(--muted)]/45"
      }`}
    >
      {option.provider === undefined ? null : (
        <ProviderIcon
          provider={option.provider}
          size={14}
          className={
            selected ? "text-[var(--primary)]" : "text-[var(--muted-foreground)]"
          }
        />
      )}
      <span
        ref={nameRef}
        className="min-w-0 truncate text-[12.5px] font-medium text-[var(--foreground)]"
      >
        {option.label}
      </span>
      {option.badge && (
        <span className="shrink-0 rounded-full bg-[var(--muted)] px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
          {option.badge}
        </span>
      )}
      <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--muted-foreground)]">
        {option.detail}
      </span>
      {option.trailing ? (
        <span className="shrink-0 text-[11px] text-[var(--muted-foreground)]">
          {option.trailing}
        </span>
      ) : null}
      {selected && (
        <Check
          size={14}
          strokeWidth={2}
          className="shrink-0 text-[var(--primary)]"
        />
      )}
      {revealFull && (
        <span className="pointer-events-none absolute inset-x-1.5 top-1/2 z-10 -translate-y-1/2 break-all rounded-lg border border-[var(--border)] bg-[var(--popover)] px-2 py-1 text-[12px] font-medium text-[var(--foreground)] shadow-md">
          {option.label}
        </span>
      )}
    </button>
  );
}

export default function ModelSelector({
  options,
  selectedKey,
  loading,
  error,
  allowSystemDefault = false,
  systemDefaultLabel,
  systemDefaultDetail,
  helperText,
  placement = "top",
  onChange,
  onRefresh,
}: {
  options: ModelPickerOption[];
  /** The selected option's key; "" selects the system/default row. */
  selectedKey: string;
  loading: boolean;
  error: boolean;
  allowSystemDefault?: boolean;
  systemDefaultLabel?: string;
  systemDefaultDetail?: string;
  helperText?: string;
  placement?: "top" | "bottom";
  /** Receives the picked option's key, or "" for the default row. */
  onChange: (key: string) => void;
  onRefresh?: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const { expanded, linger, triggerProps: lingerProps } = useLingerExpand(open);

  const selectedOption = options.find((option) => option.key === selectedKey) ?? null;

  useEffect(() => {
    if (!open) return;
    const handler = (event: MouseEvent) => {
      const target = event.target as Node;
      if (rootRef.current && !rootRef.current.contains(target)) {
        setOpen(false);
        linger();
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open, linger]);

  const defaultLabel = systemDefaultLabel || t("System default");
  const defaultDetail =
    systemDefaultDetail || t("Use the active default model from Settings");
  const canRefresh = error && Boolean(onRefresh);
  const disabled =
    loading ||
    (!canRefresh && (error || (options.length === 0 && !allowSystemDefault)));
  const label = loading
    ? t("Loading models")
    : error
      ? canRefresh
        ? t("Refresh models")
        : t("Models unavailable")
      : allowSystemDefault && !selectedKey
        ? defaultLabel
        : selectedOption?.label || t("Select model");
  const menuPlacementClass =
    placement === "bottom" ? "top-full mt-1.5" : "bottom-full mb-1.5";

  return (
    <div ref={rootRef} className="relative">
      {/* Same resting/expanded treatment as PersonaSelector: the brand
          icon is the whole control at rest; hovering (or opening) slides
          the model name out with a max-width animation and lingers ~1.2s
          after leave/selection before collapsing. */}
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          if (canRefresh) {
            setOpen(false);
            onRefresh?.();
            return;
          }
          setOpen((current) => !current);
        }}
        aria-label={canRefresh ? t("Refresh models") : t("Select model")}
        title={canRefresh ? t("Refresh models") : undefined}
        aria-expanded={open}
        {...lingerProps}
        className={`inline-flex h-8 shrink-0 items-center rounded-lg px-2 text-[14px] font-medium transition-[background-color,color,transform] duration-150 active:scale-[0.97] ${
          disabled
            ? "cursor-not-allowed text-[var(--border)]"
            : open
              ? "bg-[var(--muted)] text-[var(--foreground)]"
              : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]"
        }`}
      >
        {error ? (
          <AlertCircle size={16} strokeWidth={1.7} className="shrink-0" />
        ) : (
          <ProviderIcon provider={selectedOption?.provider} size={16} />
        )}
        <span
          className={`flex min-w-0 items-center gap-1 overflow-hidden whitespace-nowrap transition-[max-width,opacity,margin-left] duration-300 ease-out ${
            expanded
              ? "ml-1.5 max-w-[180px] opacity-100"
              : "ml-0 max-w-0 opacity-0"
          }`}
        >
          <span className="min-w-0 truncate">{label}</span>
          <ChevronDown
            size={13}
            className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
          />
        </span>
      </button>

      {open && !disabled && (
        <div
          className={`absolute right-0 z-50 ${menuPlacementClass} w-[min(280px,calc(100vw-32px))] overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--popover)] shadow-lg backdrop-blur-md`}
        >
          {helperText ? (
            <div className="border-b border-[var(--border)]/50 px-3 py-1.5 text-[11px] text-[var(--muted-foreground)]">
              {helperText}
            </div>
          ) : null}
          <div className="max-h-[280px] overflow-y-auto py-1">
            {allowSystemDefault && (
              <button
                type="button"
                title={defaultDetail}
                onClick={() => {
                  onChange("");
                  setOpen(false);
                  linger();
                }}
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors active:bg-[var(--muted)]/70 ${
                  selectedKey === ""
                    ? "bg-[var(--primary)]/[0.06]"
                    : "hover:bg-[var(--muted)]/45"
                }`}
              >
                <Bot
                  size={14}
                  strokeWidth={1.7}
                  className={`shrink-0 ${
                    selectedKey === ""
                      ? "text-[var(--primary)]"
                      : "text-[var(--muted-foreground)]"
                  }`}
                />
                <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-[var(--foreground)]">
                  {defaultLabel}
                </span>
                {selectedKey === "" && (
                  <Check
                    size={14}
                    strokeWidth={2}
                    className="shrink-0 text-[var(--primary)]"
                  />
                )}
              </button>
            )}
            {options.map((option) => (
              <PickerOptionRow
                key={option.key}
                option={option}
                selected={option.key === selectedKey}
                onSelect={() => {
                  onChange(option.key);
                  setOpen(false);
                  linger();
                }}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
