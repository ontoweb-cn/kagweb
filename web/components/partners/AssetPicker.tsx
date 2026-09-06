"use client";

/**
 * Multi-select picker for the knowledge bases a partner can be equipped
 * with. Selected items are COPIED into the partner workspace on submit —
 * the copy is the partner's own; later edits to the source don't propagate.
 *
 * The skills/notebooks asset classes were removed with those features; the
 * selection shape keeps their keys so existing call sites stay compatible.
 */

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Database } from "lucide-react";
import { listKnowledgeBases } from "@/features/knowledge/api/catalog";

export interface AssetSelection {
  knowledge_bases: string[];
  skills: string[];
  notebooks: string[];
}

interface Option {
  id: string;
  label: string;
  hint?: string;
}

function ChipGroup({
  icon: Icon,
  title,
  options,
  selected,
  onToggle,
  emptyText,
}: {
  icon: typeof Database;
  title: string;
  options: Option[];
  selected: string[];
  onToggle: (id: string) => void;
  emptyText: string;
}) {
  return (
    <div>
      <h4 className="mb-1.5 inline-flex items-center gap-1.5 text-[13px] font-medium text-[var(--muted-foreground)]">
        <Icon className="h-4 w-4" />
        {title}
        {selected.length > 0 && (
          <span className="rounded-full bg-[var(--secondary)] px-1.5 text-[11px] font-medium text-[var(--primary)]">
            {selected.length}
          </span>
        )}
      </h4>
      {options.length === 0 ? (
        <p className="text-[13px] text-[var(--muted-foreground)]">
          {emptyText}
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {options.map((option) => {
            const active = selected.includes(option.id);
            return (
              <button
                key={option.id}
                type="button"
                onClick={() => onToggle(option.id)}
                title={option.hint}
                className={`rounded-full border px-3.5 py-1.5 text-[13px] transition-all duration-150 active:scale-[0.97] ${
                  active
                    ? "border-[var(--primary)] bg-[var(--secondary)] font-medium text-[var(--primary)]"
                    : "border-[var(--border)] text-[var(--muted-foreground)] hover:border-[var(--ring)] hover:text-[var(--foreground)]"
                }`}
              >
                {option.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function AssetPicker({
  value,
  onChange,
  excluded,
}: {
  value: AssetSelection;
  onChange: (next: AssetSelection) => void;
  /** Asset ids already provisioned (hidden from the picker). */
  excluded?: Partial<AssetSelection>;
}) {
  const { t } = useTranslation();
  const [kbs, setKbs] = useState<Option[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      try {
        const kbList = await listKnowledgeBases().catch(() => []);
        setKbs(
          kbList.map((kb) => ({
            id: kb.id || kb.name,
            label: kb.name,
            hint: kb.provenance_label,
          })),
        );
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) {
    return (
      <p className="text-[13px] text-[var(--muted-foreground)]">
        {t("Loading your library…")}
      </p>
    );
  }

  const toggle = (key: keyof AssetSelection, id: string) => {
    const current = value[key];
    onChange({
      ...value,
      [key]: current.includes(id)
        ? current.filter((x) => x !== id)
        : [...current, id],
    });
  };

  const visible = (options: Option[], hidden?: string[]) =>
    hidden && hidden.length > 0
      ? options.filter(
          (o) => !hidden.includes(o.id) && !hidden.includes(o.label),
        )
      : options;

  return (
    <div className="space-y-5">
      <ChipGroup
        icon={Database}
        title={t("Knowledge bases")}
        options={visible(kbs, excluded?.knowledge_bases)}
        selected={value.knowledge_bases}
        onToggle={(id) => toggle("knowledge_bases", id)}
        emptyText={t("No knowledge bases available.")}
      />
      <p className="text-[12px] text-[var(--muted-foreground)]">
        {t("Selected items are copied into the partner's private workspace.")}
      </p>
    </div>
  );
}
