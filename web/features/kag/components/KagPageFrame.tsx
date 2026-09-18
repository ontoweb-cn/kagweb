"use client";

import { type ReactNode } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Loader2, type LucideIcon } from "lucide-react";

/**
 * KAG 管理台页面公共框架（设计 §5.4）：hub-and-spoke，`/kag` 列表页是
 * 中心，其余页面带“返回”链接。视觉沿用 /space 分节页约定。
 */

export function KagPageHeader({
  icon: Icon,
  title,
  description,
  action,
  meta,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-col gap-4 border-b border-[var(--border)]/60 pb-5 md:flex-row md:items-end md:justify-between">
      <div className="flex items-start gap-3.5">
        <span
          aria-hidden
          className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-[var(--border)]/60 bg-[var(--card)] text-[var(--foreground)] shadow-sm"
        >
          <Icon size={16} strokeWidth={1.6} />
        </span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-serif text-[19px] font-semibold leading-tight tracking-tight text-[var(--foreground)]">
              {title}
            </h1>
            {meta}
          </div>
          <p className="mt-1 max-w-xl text-[13px] leading-relaxed text-[var(--muted-foreground)]">
            {description}
          </p>
        </div>
      </div>
      {action ? <div className="shrink-0 self-start md:self-end">{action}</div> : null}
    </header>
  );
}

export function KagBackLink({ href, label }: { href: string; label: string }) {
  return (
    <Link
      href={href}
      className="group mb-5 inline-flex items-center gap-1.5 text-[13px] text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]"
    >
      <ArrowLeft
        size={15}
        strokeWidth={1.8}
        className="transition-transform group-hover:-translate-x-0.5"
      />
      {label}
    </Link>
  );
}

/** 页面主体容器（滚动 + 居中栏宽，沿 /space 分节页）。 */
export function KagPageBody({ children }: { children: ReactNode }) {
  return (
    <div className="h-full overflow-y-auto bg-[var(--background)] [scrollbar-gutter:stable]">
      <div className="mx-auto max-w-5xl px-8 py-8 pb-12">{children}</div>
    </div>
  );
}

/** 列表页的加载/出错/无权/未配置 占位视图。 */
export function KagStateView({
  loading,
  error,
}: {
  loading: boolean;
  error: string | null;
}) {
  const { t } = useTranslation();
  if (loading) {
    return (
      <div className="flex h-40 items-center justify-center gap-2 text-sm text-[var(--muted-foreground)]">
        <Loader2 size={15} className="animate-spin" />
        {t("Loading...")}
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-xl border border-[var(--border)]/60 bg-[var(--card)] px-5 py-8 text-center">
        <p className="text-[13.5px] font-medium text-[var(--foreground)]">
          {t("KAG management plane is unavailable")}
        </p>
        <p className="mx-auto mt-1.5 max-w-md break-words text-[12.5px] leading-relaxed text-[var(--muted-foreground)]">
          {error}
        </p>
      </div>
    );
  }
  return null;
}
