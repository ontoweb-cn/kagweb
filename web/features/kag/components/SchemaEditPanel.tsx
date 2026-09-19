"use client";

/**
 * M3.5 Schema 编辑面板：类型树展开区的关系增删表单。
 *
 * 编辑契约（后端 /schema/alter）：spg_type 原样回传读模型（wire 转换在
 * 服务端），增删以意图列表表达——删除不是从数组剔除（服务端要求元素级
 * DELETE 操作），由后端组装。
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link2, Plus, Trash2 } from "lucide-react";

import { isApiError } from "@/shared/api/errors";
import { alterKagProjectSchema } from "../api";
import { parseRelations, type SpgTypeRow } from "../model";

interface SchemaEditPanelProps {
  projectId: string;
  typeRow: SpgTypeRow;
  /** schema 内全部类型（object type 下拉；排除自身）。 */
  allTypes: SpgTypeRow[];
  onAltered: () => void;
}

export function SchemaEditPanel({
  projectId,
  typeRow,
  allTypes,
  onAltered,
}: SchemaEditPanelProps) {
  const { t } = useTranslation();
  const [deleting, setDeleting] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [nameZh, setNameZh] = useState("");
  const [objectTypeName, setObjectTypeName] = useState("");
  const [desc, setDesc] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const relations = parseRelations(typeRow.raw.relations);
  const candidates = allTypes.filter(
    (row) => row.kind !== "basic" && row.key !== typeRow.key,
  );

  async function submit(add: boolean) {
    setError("");
    if (add) {
      if (!name.trim() || !objectTypeName) {
        setError(t("Relation name and object type are required"));
        return;
      }
    } else if (deleting.length === 0) {
      return;
    }
    setBusy(true);
    try {
      await alterKagProjectSchema(projectId, {
        spg_type: typeRow.raw,
        add_relations: add
          ? [
              {
                name: name.trim(),
                name_zh: nameZh.trim(),
                desc: desc.trim(),
                object_type_name: objectTypeName,
              },
            ]
          : undefined,
        delete_relations: add ? undefined : deleting,
      });
      setDeleting([]);
      setName("");
      setNameZh("");
      setDesc("");
      onAltered();
    } catch (err) {
      setError(
        isApiError(err) && err.message
          ? err.message
          : t("The schema change was rejected"),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 space-y-3 rounded-lg border border-[var(--border)]/50 bg-[var(--background)]/40 p-3">
      <div className="flex items-center gap-1.5 text-[11.5px] font-semibold text-[var(--muted-foreground)]">
        <Link2 size={12} aria-hidden />
        {t("Relations")}
      </div>
      {relations.length === 0 ? (
        <p className="px-1 text-[11.5px] text-[var(--muted-foreground)]">
          {t("No relations")}
        </p>
      ) : (
        <div className="space-y-1">
          {relations.map((rel) => {
            const marked = deleting.includes(rel.name);
            return (
              <div
                key={rel.name}
                className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-[12px] transition-colors ${
                  marked
                    ? "bg-red-500/10 opacity-60"
                    : "bg-[var(--card)]/60"
                }`}
              >
                <span className="min-w-0 flex-1 truncate font-mono text-[var(--foreground)]">
                  {rel.name}
                </span>
                <span className="shrink-0 truncate font-mono text-[11px] text-[var(--muted-foreground)]">
                  {rel.objectType}
                </span>
                {rel.inherited ? null : (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      setDeleting((prev) =>
                        marked
                          ? prev.filter((n) => n !== rel.name)
                          : [...prev, rel.name],
                      )
                    }
                    aria-label={t("Mark relation for deletion")}
                    className="shrink-0 rounded p-1 text-[var(--muted-foreground)] transition-colors hover:bg-red-500/10 hover:text-red-600 disabled:opacity-40"
                  >
                    <Trash2 size={12} />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
      {deleting.length > 0 ? (
        <button
          type="button"
          disabled={busy}
          onClick={() => submit(false)}
          className="w-full rounded-md bg-red-600 px-3 py-1.5 text-[12px] font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-50"
        >
          {busy
            ? t("Submitting…")
            : t("Delete {{count}} relation(s)", { count: deleting.length })}
        </button>
      ) : null}

      <div className="space-y-2 border-t border-[var(--border)]/40 pt-3">
        <div className="flex items-center gap-1.5 text-[11.5px] font-semibold text-[var(--muted-foreground)]">
          <Plus size={12} aria-hidden />
          {t("Add a relation")}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <input
            value={name}
            disabled={busy}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("Relation name (e.g. workFor)")}
            className="col-span-2 rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 font-mono text-[12px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
          />
          <input
            value={nameZh}
            disabled={busy}
            onChange={(e) => setNameZh(e.target.value)}
            placeholder={t("Display name (Chinese)")}
            className="rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 text-[12px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
          />
          <select
            value={objectTypeName}
            disabled={busy}
            onChange={(e) => setObjectTypeName(e.target.value)}
            className="rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 text-[12px] text-[var(--foreground)] outline-none focus:border-[var(--foreground)]/30"
          >
            <option value="">{t("Object type")}</option>
            {candidates.map((row) => (
              <option key={row.key} value={row.name}>
                {row.name}
              </option>
            ))}
          </select>
          <input
            value={desc}
            disabled={busy}
            onChange={(e) => setDesc(e.target.value)}
            placeholder={t("Description (optional)")}
            className="col-span-2 rounded-md border border-[var(--border)]/60 bg-[var(--card)] px-2.5 py-1.5 text-[12px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60 focus:border-[var(--foreground)]/30"
          />
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => submit(true)}
          className="w-full rounded-md bg-[var(--primary)] px-3 py-1.5 text-[12px] font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {busy ? t("Submitting…") : t("Add relation")}
        </button>
      </div>

      {error ? (
        <p className="rounded-md bg-red-500/10 px-2.5 py-1.5 text-[11.5px] text-red-600 dark:text-red-400">
          {error}
        </p>
      ) : null}
    </div>
  );
}
