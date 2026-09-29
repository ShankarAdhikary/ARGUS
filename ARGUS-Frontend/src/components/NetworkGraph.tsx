import cytoscape, { Core, EdgeSingular, ElementDefinition, NodeSingular } from "cytoscape";
import { useEffect, useRef, useState } from "react";
import type { GraphElements } from "../types";

// Nova palette — matches CSS design tokens (purple/pink/blue/green)
const TYPE_COLOR: Record<string, string> = {
  person:            "#7c5cfc",   // --purple
  organization:      "#00e676",   // --green
  location:          "#ffd740",   // --amber
  vehicle:           "#90a4ae",   // slate
  financial_account: "#e040fb",   // --pink
  event:             "#ff5252",   // --red
  phone:             "#4fc3f7",   // --blue
  fir:               "#94a3b8",   // light slate
};

const TYPE_SHAPE: Record<string, string> = {
  person:            "ellipse",
  phone:             "round-rectangle",
  fir:               "diamond",
  financial_account: "hexagon",
  organization:      "rectangle",
  location:          "triangle",
  vehicle:           "barrel",
  event:             "star",
};

const FIR_COLLAPSE_AT = 12;

interface Props {
  elements: GraphElements;
  height?: number;
  onNodeClick?: (id: string) => void;
  onNodeDoubleClick?: (id: string) => void;
  selectedId?: string | null;
}

export default function NetworkGraph({ elements, height = 460, onNodeClick, onNodeDoubleClick, selectedId }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const [tooltip, setTooltip] = useState<{ label: string; type: string; x: number; y: number } | null>(null);
  const [zoom, setZoom] = useState(1);
  // Suspects can link to dozens of FIRs; showing them all buries the real structure,
  // so leaf FIRs are collapsed by default once there are more than FIR_COLLAPSE_AT of them.
  const [showFirs, setShowFirs] = useState(false);
  const [firLeafCount, setFirLeafCount] = useState(0);

  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements: toCyElements(elements),
      style: [
        {
          selector: "node",
          style: {
            "background-color":   (ele: NodeSingular) => TYPE_COLOR[ele.data("type")] ?? "#64748b",
            "shape":              (ele: NodeSingular) => (TYPE_SHAPE[ele.data("type")] ?? "ellipse") as "ellipse",
            label: (ele: NodeSingular) => {
              const hidden = Number(ele.data("hiddenFirs") ?? 0);
              return hidden ? `${ele.data("label")}\n+${hidden} FIRs` : String(ele.data("label"));
            },
            color:                "#e2e8f0",
            "font-size":          11,
            "font-weight":        600,
            "text-wrap":          "wrap",
            "text-valign":        "bottom",
            "text-margin-y":      6,
            "text-outline-width": 2.5,
            "text-outline-color": "#0b1120",
            "min-zoomed-font-size": 7,
            width:  (ele: NodeSingular) => nodeSize(ele),
            height: (ele: NodeSingular) => nodeSize(ele),
            "border-width":  1.5,
            "border-color":  "rgba(255,255,255,.35)",
            "background-opacity": 1,
          },
        },
        { selector: "node.hidden", style: { display: "none" } },
        { selector: 'node[type = "fir"]', style: { "font-size": 9, "font-weight": 500, color: "#94a3b8", "min-zoomed-font-size": 10 } },
        {
          selector: "node:selected",
          style: {
            "border-width": 3,
            "border-color": "#7c5cfc",
            "border-opacity": 1,
            "background-opacity": 1,
          },
        },
        {
          selector: "node.dimmed",
          style: {
            "background-opacity": 0.2,
            "text-opacity": 0.2,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.6,
            "line-color":             "rgba(148,163,255,.5)",
            "line-style":             (ele: EdgeSingular) => (ele.data("direct") ? "solid" : "dashed"),
            "curve-style":            "bezier",
            "target-arrow-shape":     "triangle",
            "target-arrow-color":     "rgba(148,163,255,.7)",
            "arrow-scale":            0.8,
            label:                    "",
            "font-size":              9,
            color:                    "#cbd5e1",
            "text-rotation":          "autorotate",
            "text-outline-width":     1,
            "text-outline-color":     "#080812",
          },
        },
        {
          selector: "edge.highlighted",
          style: {
            "line-color":         "#a5b4fc",
            "target-arrow-color": "#a5b4fc",
            label:                "data(label)",
            width:                3,
            "z-index":            10,
          },
        },
        {
          selector: "edge.dimmed",
          style: {
            opacity: 0.08,
          },
        },
      ],
      minZoom: 0.12,
      maxZoom: 4,
    });

    // Collapse leaf FIRs (a single neighbour) into their parent's "+N FIRs" badge when there are many.
    const leaves = cy.nodes('[type = "fir"]').filter((n) => n.neighborhood("node").length <= 1);
    setFirLeafCount(leaves.length);
    if (leaves.length > FIR_COLLAPSE_AT && !showFirs) {
      leaves.forEach((leaf) => {
        const parent = leaf.neighborhood("node").first();
        if (parent.nonempty()) parent.data("hiddenFirs", Number(parent.data("hiddenFirs") ?? 0) + 1);
        leaf.addClass("hidden");
      });
    }
    cy.elements().not(".hidden").layout({
      name:            "cose",
      animate:         false,
      fit:             true,
      padding:         50,
      nodeRepulsion:   () => 22000,
      idealEdgeLength: () => 120,
      edgeElasticity:  () => 80,
      nodeOverlap:     24,
      componentSpacing: 90,
      numIter:         1500,
      gravity:         0.35,
      randomize:       true,
    } as cytoscape.LayoutOptions).run();

    // Node hover → tooltip
    cy.on("mouseover", "node", (evt) => {
      const node = evt.target as NodeSingular;
      const pos = node.renderedPosition();
      const container = containerRef.current!;
      const rect = container.getBoundingClientRect();
      setTooltip({
        label: node.data("label") as string,
        type:  node.data("type")  as string,
        x:     pos.x + rect.left - container.offsetLeft,
        y:     pos.y + rect.top  - container.offsetTop - 38,
      });
    });
    cy.on("mouseout", "node", () => setTooltip(null));

    // Track zoom
    cy.on("zoom", () => setZoom(cy.zoom()));

    // Click handler — highlight connected edges
    cy.on("tap", "node", (evt) => {
      const node = evt.target as NodeSingular;
      const connected = node.connectedEdges();
      cy.elements().addClass("dimmed");
      node.removeClass("dimmed").addClass("selected");
      connected.removeClass("dimmed").addClass("highlighted");
      connected.connectedNodes().removeClass("dimmed");
      if (onNodeClick) onNodeClick(node.id());
    });

    // Tap on background → reset
    cy.on("tap", (evt) => {
      if (evt.target === cy) {
        cy.elements().removeClass("dimmed highlighted");
      }
    });

    if (onNodeDoubleClick) cy.on("dbltap", "node", (evt) => onNodeDoubleClick((evt.target as NodeSingular).id()));

    cyRef.current = cy;
    return () => {
      setTooltip(null);
      cy.destroy();
      cyRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elements, showFirs]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().unselect();
    if (selectedId) cy.getElementById(selectedId).select();
  }, [selectedId]);

  return (
    <div style={{ position: "relative", width: "100%", height }}>
      {/* Graph canvas */}
      <div
        ref={containerRef}
        style={{
          width: "100%",
          height: "100%",
          background: "#0b1120",
          borderRadius: "var(--radius-md)",
          border: "1px solid rgba(255,255,255,.06)",
        }}
      />

      {/* Tooltip */}
      {tooltip && (
        <div
          style={{
            position: "absolute",
            left:     tooltip.x,
            top:      tooltip.y,
            transform: "translateX(-50%)",
            pointerEvents: "none",
            background: "#1a1a2e",
            border: "1px solid rgba(124,92,252,.4)",
            borderRadius: 6,
            padding: "5px 10px",
            fontSize: "0.75rem",
            color: "#c8c8d8",
            whiteSpace: "nowrap",
            zIndex: 20,
            boxShadow: "0 4px 16px rgba(0,0,0,.5)",
          }}
        >
          <span style={{ color: TYPE_COLOR[tooltip.type] ?? "#aaa", fontWeight: 700, textTransform: "capitalize" }}>
            {tooltip.type.replace("_", " ")}
          </span>{" · "}
          {tooltip.label}
        </div>
      )}

      {/* Zoom controls */}
      <div style={{ position: "absolute", bottom: 12, right: 12, display: "flex", gap: 4, zIndex: 10 }}>
        {[
          { label: "+", title: "Zoom in",  fn: () => cyRef.current?.zoom({ level: cyRef.current.zoom() * 1.3, renderedPosition: { x: (containerRef.current?.clientWidth ?? 600) / 2, y: (containerRef.current?.clientHeight ?? 400) / 2 } }) },
          { label: "−", title: "Zoom out", fn: () => cyRef.current?.zoom({ level: cyRef.current.zoom() / 1.3, renderedPosition: { x: (containerRef.current?.clientWidth ?? 600) / 2, y: (containerRef.current?.clientHeight ?? 400) / 2 } }) },
          { label: "⊞", title: "Fit all",  fn: () => cyRef.current?.fit(undefined, 32) },
        ].map(({ label, title, fn }) => (
          <button
            key={label}
            title={title}
            type="button"
            onClick={fn}
            style={{
              width: 28, height: 28, borderRadius: 6, border: "1px solid rgba(124,92,252,.3)",
              background: "rgba(10,10,20,.85)", color: "#9988ff", fontSize: 14, display: "flex",
              alignItems: "center", justifyContent: "center", cursor: "pointer", padding: 0,
              backdropFilter: "blur(4px)",
            }}
          >
            {label}
          </button>
        ))}
        <span style={{ fontSize: "0.75rem", color: "#94a3b8", alignSelf: "center", paddingLeft: 4 }}>
          {(zoom * 100).toFixed(0)}%
        </span>
      </div>

      {/* FIR collapse toggle */}
      {firLeafCount > FIR_COLLAPSE_AT && (
        <button
          type="button"
          onClick={() => setShowFirs((v) => !v)}
          aria-pressed={showFirs}
          style={{
            position: "absolute", top: 10, right: 10, zIndex: 10, padding: "5px 11px", fontSize: "0.75rem",
            borderRadius: 6, border: "1px solid rgba(165,180,252,.4)", background: "rgba(10,14,28,.9)", color: "#c7d2fe", cursor: "pointer",
          }}
        >
          {showFirs ? `Hide ${firLeafCount} FIRs` : `Show ${firLeafCount} FIRs`}
        </button>
      )}

      {/* Legend */}
      <div style={{ position: "absolute", top: 10, left: 10, display: "flex", flexWrap: "wrap", gap: "4px 8px", maxWidth: 260, zIndex: 10, background: "rgba(10,14,28,.7)", padding: "5px 8px", borderRadius: 6 }}>
        {Object.entries(TYPE_COLOR).filter(([t]) => elements.nodes.some(n => n.data.type === t)).map(([type, color]) => (
          <span key={type} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: "0.75rem", color: "#cbd5e1" }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: color, flexShrink: 0, display: "inline-block" }} />
            {type.replace("_", " ")}
          </span>
        ))}
      </div>
    </div>
  );
}

function nodeSize(ele: NodeSingular): number {
  const type = ele.data("type") as string;
  const conf = Number(ele.data("confidence") ?? 0.5);
  if (type === "fir") return 14;
  const base = type === "person" ? 30 : type === "phone" ? 24 : 24;
  return base + conf * 12;
}

function toCyElements(elements: GraphElements): ElementDefinition[] {
  const nodes: ElementDefinition[] = elements.nodes.map((n) => ({ data: { ...n.data } }));
  const edges: ElementDefinition[] = elements.edges.map((e) => ({ data: { ...e.data } }));
  return [...nodes, ...edges];
}
