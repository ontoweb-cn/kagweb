'use client'

/**
 * Force-directed canvas for a GraphRAG retrieval subgraph.
 *
 * Cytoscape is browser-only, so it is imported from the mount effect. The
 * inline viewer stays compact; the expand control opens a document-level
 * overlay so the graph can escape the activity panel's transformed layout.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { Core, ElementDefinition, EventObject, StylesheetStyle } from 'cytoscape'
import { LocateFixed, Maximize2, Minimize2, Scan, ZoomIn, ZoomOut } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { ToolGraphSubgraph } from '@/lib/session-activity'

type GraphVariant = 'inline' | 'fullscreen'

type GraphSelection =
  | { kind: 'node'; id: string; title: string; detail: string; meta: string }
  | {
      kind: 'edge'
      id: string
      title: string
      detail: string
      meta: string
    }
  | null

type GraphHover = {
  x: number
  y: number
  selection: Exclude<GraphSelection, null>
} | null

type FcoseLayoutOptions = import('cytoscape').BaseLayoutOptions & {
  quality?: 'draft' | 'default' | 'proof'
  randomize?: boolean
  nodeDimensionsIncludeLabels?: boolean
  nodeSeparation?: number
  tile?: boolean
  padding?: number
  nodeRepulsion?: (node: unknown) => number
  idealEdgeLength?: (edge: unknown) => number
  edgeElasticity?: number
  nestingFactor?: number
  gravity?: number
  numIter?: number
  initialEnergyOnIncremental?: number
}

const ZOOM_MIN = 0.18
const ZOOM_MAX = 2.4

let fcoseRegistered = false

function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

function graphMetrics(variant: GraphVariant): {
  fontSize: number
  labelLimit: number
  maxLabelWidth: number
} {
  return variant === 'fullscreen'
    ? { fontSize: 10.5, labelLimit: 42, maxLabelWidth: 92 }
    : { fontSize: 8.5, labelLimit: 14, maxLabelWidth: 54 }
}

function graphLayoutMetrics(variant: GraphVariant, width: number, height: number) {
  const side = Math.max(240, Math.min(width, height))
  const idealEdgeLength = Math.max(68, Math.min(140, side * 0.15))

  return {
    idealEdgeLength,
    nodeRepulsion: (idealEdgeLength / 50) ** 2 * 2200,
    nodeSeparation: idealEdgeLength * 1.05,
    numIter: variant === 'fullscreen' ? 3200 : 2800,
    padding: variant === 'fullscreen' ? 44 : 32,
    gravity: variant === 'fullscreen' ? 0.5 : 0.65,
  }
}

function hashGraph(graph: ToolGraphSubgraph): number {
  let hash = 2166136261
  for (const node of graph.nodes) hash = Math.imul(hash ^ node.id.length, 16777619)
  for (const edge of graph.edges) hash = Math.imul(hash ^ Math.round(edge.weight * 255), 16777619)
  return hash
}

function mulberry32(seed: number): () => number {
  let value = seed
  return () => {
    value |= 0
    value = (value + 0x6d2b79f5) | 0
    let mixed = Math.imul(value ^ (value >>> 15), 1 | value)
    mixed = (mixed + Math.imul(mixed ^ (mixed >>> 7), 61 | mixed)) ^ mixed
    return ((mixed ^ (mixed >>> 14)) >>> 0) / 4294967296
  }
}

/** Seed fcose with a viewport-shaped grid so dense graphs do not fan out randomly. */
function initialGraphPositions(
  graph: ToolGraphSubgraph,
  width: number,
  height: number
): Map<string, { x: number; y: number }> {
  const count = Math.max(1, graph.nodes.length)
  const aspect = Math.max(0.5, Math.min(2.2, width / Math.max(1, height)))
  const columns = Math.max(1, Math.min(10, Math.round(Math.sqrt(count * aspect))))
  const rows = Math.max(1, Math.ceil(count / columns))
  const layoutWidth = Math.max(560, width * 1.35)
  const layoutHeight = Math.max(500, height * 1.35)
  const stepX = columns > 1 ? (layoutWidth - 120) / (columns - 1) : 0
  const stepY = rows > 1 ? (layoutHeight - 120) / (rows - 1) : 0
  const random = mulberry32(hashGraph(graph))

  return new Map(
    graph.nodes.map((node, index) => {
      const column = index % columns
      const row = Math.floor(index / columns)
      return [
        node.id,
        {
          x: 60 + column * stepX + (random() - 0.5) * Math.max(18, stepX * 0.2),
          y: 60 + row * stepY + (random() - 0.5) * Math.max(18, stepY * 0.2),
        },
      ]
    })
  )
}

function buildStylesheet(variant: GraphVariant): StylesheetStyle[] {
  const primary = cssVar('--primary', '#3b82f6')
  const primaryFg = cssVar('--primary-foreground', '#ffffff')
  const mutedFg = cssVar('--muted-foreground', '#737373')
  const border = cssVar('--border', '#e5e5e5')
  const card = cssVar('--card', '#ffffff')
  const { fontSize, maxLabelWidth } = graphMetrics(variant)

  return [
    {
      selector: 'node',
      style: {
        label: 'data(label)',
        'text-valign': 'bottom',
        'text-halign': 'center',
        'text-wrap': 'wrap',
        'text-max-width': `${maxLabelWidth}`,
        'font-size': fontSize,
        color: mutedFg,
        'background-color': card,
        'background-opacity': 0.94,
        'border-width': 1,
        'border-color': border,
        width: 'mapData(degree, 0, 10, 17, 36)',
        height: 'mapData(degree, 0, 10, 17, 36)',
        'font-weight': 500,
        'text-background-color': card,
        'text-background-opacity': 0.76,
        'text-background-padding': '2px',
        'text-margin-y': 2,
      },
    },
    {
      selector: 'node:selected',
      style: {
        'background-color': primary,
        'border-color': primary,
        'border-width': 2,
        color: primaryFg,
        'font-weight': 700,
      },
    },
    {
      selector: 'edge',
      style: {
        'curve-style': 'haystack',
        'haystack-radius': 0.22,
        'line-color': border,
        'line-opacity': 0.42,
        width: 'mapData(weight, 0, 10, 1, 3.4)',
      },
    },
    {
      selector: 'edge:selected',
      style: {
        'line-color': primary,
        'line-opacity': 0.94,
        width: 'mapData(weight, 0, 10, 2, 4.5)',
      },
    },
  ]
}

function toElements(
  graph: ToolGraphSubgraph,
  labelLimit: number,
  positions: Map<string, { x: number; y: number }>
): ElementDefinition[] {
  const nodes = graph.nodes.map(node => ({
    group: 'nodes' as const,
    data: {
      id: node.id,
      // Long GraphRAG entity names dominate a small force layout. The full
      // title remains available through the selection card below the canvas.
      label:
        node.label.length > labelLimit
          ? `${node.label.slice(0, labelLimit).trimEnd()}…`
          : node.label,
      type: node.type,
      description: node.description,
      degree: node.degree,
    },
    position: positions.get(node.id),
  }))
  const edges = graph.edges.map((edge, index) => ({
    group: 'edges' as const,
    data: {
      id: `${edge.source}->${edge.target}:${index}`,
      source: edge.source,
      target: edge.target,
      description: edge.description,
      weight: edge.weight,
    },
  }))
  return [...nodes, ...edges]
}

function ControlButton({
  label,
  onClick,
  children,
}: {
  label: string
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]"
    >
      {children}
    </button>
  )
}

function GraphCanvas({
  graph,
  heightClass,
  variant = 'inline',
  onToggleExpand,
  rootClassName = '',
  onTrace,
}: {
  graph: ToolGraphSubgraph
  heightClass: string
  variant?: GraphVariant
  onToggleExpand?: () => void
  rootClassName?: string
  onTrace?: () => void
}) {
  const { t } = useTranslation()
  const containerRef = useRef<HTMLDivElement | null>(null)
  const cyRef = useRef<Core | null>(null)
  const [zoom, setZoom] = useState(100)
  const [selection, setSelection] = useState<GraphSelection>(null)
  const [hover, setHover] = useState<GraphHover>(null)
  const graphKey = useMemo(
    () => JSON.stringify([graph.nodes, graph.edges]),
    [graph.nodes, graph.edges]
  )

  useEffect(() => {
    setSelection(null)
    setHover(null)
  }, [graphKey])

  useEffect(() => {
    let disposed = false

    ;(async () => {
      const [cytoscape, fcose] = await Promise.all([
        import('cytoscape').then(module => module.default),
        import('cytoscape-fcose').then(module => module.default),
      ])
      if (disposed || !containerRef.current) return
      if (!fcoseRegistered) {
        fcose(cytoscape)
        fcoseRegistered = true
      }
      const rect = containerRef.current.getBoundingClientRect()
      const layoutMetrics = graphLayoutMetrics(variant, rect.width, rect.height)
      const initialPositions = initialGraphPositions(graph, rect.width, rect.height)
      const cy = cytoscape({
        container: containerRef.current,
        elements: toElements(graph, graphMetrics(variant).labelLimit, initialPositions),
        style: buildStylesheet(variant),
        layout: {
          name: 'fcose',
          quality: 'proof',
          animate: false,
          randomize: false,
          nodeDimensionsIncludeLabels: true,
          nodeSeparation: layoutMetrics.nodeSeparation,
          tile: false,
          padding: layoutMetrics.padding,
          nodeRepulsion: () => layoutMetrics.nodeRepulsion,
          idealEdgeLength: () => layoutMetrics.idealEdgeLength,
          edgeElasticity: 0.45,
          nestingFactor: 0.2,
          gravity: layoutMetrics.gravity,
          numIter: layoutMetrics.numIter,
          initialEnergyOnIncremental: 0.3,
        } as FcoseLayoutOptions,
        minZoom: ZOOM_MIN,
        maxZoom: ZOOM_MAX,
        wheelSensitivity: 0.18,
      })
      cyRef.current = cy
      cy.fit(undefined, 28)
      const maxInitialZoom = variant === 'fullscreen' ? 1.2 : 1.05
      if (cy.zoom() > maxInitialZoom) {
        cy.zoom(maxInitialZoom)
        cy.center()
      }
      setZoom(Math.round(cy.zoom() * 100))
      cy.on('zoom', () => setZoom(Math.round(cy.zoom() * 100)))

      cy.on('tap', 'node', (event: EventObject) => {
        const rendered = event.renderedPosition
        const node = graph.nodes.find(item => item.id === event.target.id())
        if (!node) return
        const nextSelection = {
          kind: 'node',
          id: node.id,
          title: node.label,
          detail: node.description,
          meta: node.type,
        } satisfies Exclude<GraphSelection, null>
        setSelection(nextSelection)
        setHover({ x: rendered.x, y: rendered.y, selection: nextSelection })
      })
      cy.on('tap', 'edge', (event: EventObject) => {
        const rendered = event.renderedPosition
        const source = String(event.target.source().id())
        const target = String(event.target.target().id())
        const edge = graph.edges.find(item => item.source === source && item.target === target)
        if (!edge) return
        const sourceLabel = graph.nodes.find(node => node.id === source)?.label ?? source
        const targetLabel = graph.nodes.find(node => node.id === target)?.label ?? target
        const nextSelection = {
          kind: 'edge',
          id: `${source}->${target}`,
          title: `${sourceLabel} → ${targetLabel}`,
          detail: edge.description,
          meta: '',
        } satisfies Exclude<GraphSelection, null>
        setSelection(nextSelection)
        setHover({ x: rendered.x, y: rendered.y, selection: nextSelection })
      })
      cy.on('mouseover', 'node, edge', (event: EventObject) => {
        const rendered = event.renderedPosition
        if ((event.target as { isNode: () => boolean }).isNode()) {
          const node = graph.nodes.find(item => item.id === event.target.id())
          if (!node) return
          setHover({
            x: rendered.x,
            y: rendered.y,
            selection: {
              kind: 'node',
              id: node.id,
              title: node.label,
              detail: node.description,
              meta: node.type,
            },
          })
          return
        }

        const source = String(event.target.source().id())
        const target = String(event.target.target().id())
        const edge = graph.edges.find(item => item.source === source && item.target === target)
        if (!edge) return
        const sourceLabel = graph.nodes.find(node => node.id === source)?.label ?? source
        const targetLabel = graph.nodes.find(node => node.id === target)?.label ?? target
        setHover({
          x: rendered.x,
          y: rendered.y,
          selection: {
            kind: 'edge',
            id: `${source}->${target}`,
            title: `${sourceLabel} → ${targetLabel}`,
            detail: edge.description,
            meta: t('Relationship'),
          },
        })
      })
      cy.on('mouseout', 'node, edge', () => setHover(null))
      cy.on('tap', (event: EventObject) => {
        if (event.target === cy) setSelection(null)
      })
    })()

    return () => {
      disposed = true
      cyRef.current?.destroy()
      cyRef.current = null
    }
    // Graph payloads are normalized and bounded; a payload replacement is
    // rare enough to justify rebuilding this lightweight canvas.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphKey, variant])

  const zoomBy = useCallback((factor: number) => {
    const cy = cyRef.current
    if (!cy) return
    cy.stop()
    cy.zoom(Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, cy.zoom() * factor)))
    cy.center()
  }, [])

  const fitGraph = useCallback(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.fit(undefined, 36)
    cy.center()
  }, [])

  return (
    <div className={`relative flex min-h-0 w-full flex-col bg-[var(--card)] ${rootClassName}`}>
      <div className="relative min-h-0 flex-1">
        <div
          ref={containerRef}
          className={`${heightClass} w-full overflow-hidden`}
          role="img"
          aria-label={`${graph.nodes.length} entities and ${graph.edges.length} relationships`}
          style={{
            backgroundImage:
              'radial-gradient(circle at 18% 14%, color-mix(in srgb, var(--primary) 5%, transparent), transparent 34%), radial-gradient(circle at 82% 78%, color-mix(in srgb, var(--foreground) 4%, transparent), transparent 36%)',
          }}
        />

        {hover ? (
          <div
            className="pointer-events-none absolute z-20 max-w-[min(360px,88%)] rounded-xl border border-[var(--border)]/70 bg-[var(--card)]/96 p-2.5 shadow-[0_10px_28px_color-mix(in_srgb,var(--foreground)_12%,transparent)] backdrop-blur"
            style={{
              left: hover.x,
              top: hover.y,
              transform: 'translate(-50%, calc(-100% - 10px))',
            }}
          >
            <div className="break-words text-[11.5px] font-semibold text-[var(--foreground)]">
              {hover.selection.title}
            </div>
            {hover.selection.meta ? (
              <div className="mt-1 text-[10px] font-medium uppercase tracking-[0.04em] text-[var(--muted-foreground)]">
                {hover.selection.meta}
              </div>
            ) : null}
            {hover.selection.detail ? (
              <div className="mt-1.5 max-h-[180px] overflow-y-auto text-[11px] leading-[1.55] text-[var(--muted-foreground)]">
                {hover.selection.detail}
              </div>
            ) : (
              <div className="mt-1.5 text-[11px] italic text-[var(--muted-foreground)]/70">
                {t('No additional details')}
              </div>
            )}
          </div>
        ) : null}

        <div className="absolute right-2 top-2 z-10 flex items-center gap-0.5 rounded-xl border border-[var(--border)]/60 bg-[var(--card)]/88 p-0.5 shadow-[0_6px_18px_color-mix(in_srgb,var(--foreground)_7%,transparent)] backdrop-blur">
          <ControlButton label={t('Zoom in')} onClick={() => zoomBy(1.22)}>
            <ZoomIn size={13} strokeWidth={1.9} />
          </ControlButton>
          <ControlButton label={t('Zoom out')} onClick={() => zoomBy(1 / 1.22)}>
            <ZoomOut size={13} strokeWidth={1.9} />
          </ControlButton>
          <ControlButton label={t('Fit view')} onClick={fitGraph}>
            <Scan size={13} strokeWidth={1.9} />
          </ControlButton>
          <span className="w-9 px-1 text-center text-[10px] font-semibold tabular-nums text-[var(--muted-foreground)]">
            {zoom}%
          </span>
          {onToggleExpand ? (
            <ControlButton
              label={variant === 'fullscreen' ? t('Collapse graph') : t('Expand graph')}
              onClick={onToggleExpand}
            >
              {variant === 'fullscreen' ? (
                <Minimize2 size={13} strokeWidth={1.9} />
              ) : (
                <Maximize2 size={13} strokeWidth={1.9} />
              )}
            </ControlButton>
          ) : null}
          {onTrace ? (
            <ControlButton label={t('Locate related answer')} onClick={onTrace}>
              <LocateFixed size={13} strokeWidth={1.9} />
            </ControlButton>
          ) : null}
        </div>
      </div>

      <div
        className={`shrink-0 border-t border-[var(--border)]/35 bg-[var(--background)] px-3 text-[10.5px] leading-[1.5] text-[var(--muted-foreground)] ${
          variant === 'fullscreen' ? 'min-h-[64px] py-3' : 'min-h-[42px] py-2'
        }`}
      >
        {selection ? (
          <>
            <div className="truncate text-[11.5px] font-semibold text-[var(--foreground)]">
              {selection.title}
              {selection.meta ? (
                <span className="ml-1.5 font-normal text-[var(--muted-foreground)]">
                  · {selection.meta}
                </span>
              ) : null}
            </div>
            {selection.detail ? (
              <div
                className={`mt-1 break-words ${
                  variant === 'fullscreen' ? 'line-clamp-4' : 'line-clamp-2'
                }`}
              >
                {selection.detail}
              </div>
            ) : null}
          </>
        ) : (
          <span className="opacity-72">
            {graph.nodes.length} {t('Graph entities')} · {graph.edges.length}{' '}
            {t('Graph relationships')}
          </span>
        )}
      </div>
    </div>
  )
}

export default function GraphSubgraphView({
  graph,
  onTrace,
}: {
  graph: ToolGraphSubgraph
  onTrace?: () => void
}) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    if (!expanded) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setExpanded(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [expanded])

  if (expanded) {
    return createPortal(
      <div className="fixed inset-0 z-[200] flex items-center justify-center bg-[color-mix(in_srgb,var(--foreground)_18%,transparent)] p-3 backdrop-blur-sm sm:p-6">
        <div className="flex h-full max-h-[920px] w-full max-w-[1480px] flex-col overflow-hidden rounded-2xl border border-[var(--border)]/65 bg-[var(--card)] shadow-2xl">
          <div className="flex shrink-0 items-center gap-2 border-b border-[var(--border)]/45 bg-[color-mix(in_srgb,var(--muted)_24%,var(--card))] px-3 py-2.5">
            <div className="min-w-0 flex-1 text-[12.5px] font-semibold text-[var(--foreground)]">
              {t('Graph preview')}
            </div>
            <button
              type="button"
              onClick={() => setExpanded(false)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)]/60 px-2.5 py-1 text-[11px] font-medium text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/45 hover:text-[var(--foreground)]"
            >
              <Minimize2 size={12} strokeWidth={2} />
              {t('Collapse graph')}
            </button>
          </div>
          <GraphCanvas
            graph={graph}
            heightClass="h-full"
            variant="fullscreen"
            onToggleExpand={() => setExpanded(false)}
            rootClassName="min-h-0 flex-1"
            onTrace={onTrace}
          />
        </div>
      </div>,
      document.body
    )
  }

  return (
    <div className="overflow-hidden border-t border-[var(--border)]/45">
      <GraphCanvas
        graph={graph}
        heightClass="h-[min(50vh,470px)]"
        variant="inline"
        onToggleExpand={() => setExpanded(true)}
        onTrace={onTrace}
      />
    </div>
  )
}
