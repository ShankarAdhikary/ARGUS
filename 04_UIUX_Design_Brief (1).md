# UI/UX Design Brief
## ARGUS — AI-Powered Criminal Network Analysis System

---

## 1. Design Principles

1. **Clarity over decoration** — investigators use this under time pressure; every screen should answer "what am I looking at, how sure is the system, and where did it come from" at a glance.
2. **Confidence is always visible** — no AI-derived fact is ever shown without a confidence indicator and source citation, distinguished visually from verified/source-confirmed facts.
3. **Progressive disclosure** — dense graph/data screens default to a simplified view (top connections, highest-confidence patterns) with clear affordances to drill deeper, rather than overwhelming on first load.
4. **Consistency across roles** — the same visual language (color, iconography, card structure) is used whether an investigator, analyst, or supervisor is viewing an entity, so switching context doesn't require relearning the UI.
5. **Field-ready** — usable on mid-range Android devices over 3G/4G, with a responsive layout that degrades gracefully (list views before graph views on constrained connections).
6. **Restraint in sensitive contexts** — no case-photo thumbnails, no sensationalized iconography (e.g., no red skulls/warning-siren graphics for suspects); the tone is clinical and procedural, appropriate for evidentiary use and for the dignity of victims referenced in the data.

---

## 2. Color Palette

A restrained, low-saturation palette suited to long analytical sessions and formal government use — avoiding the "hacker dashboard" cliché of neon-on-black.

| Role | Color | Hex | Usage |
|---|---|---|---|
| Primary (brand/action) | Deep Indigo | `#1E2A4A` | Primary buttons, nav bar, headers |
| Secondary (accent) | Slate Teal | `#3B7A8C` | Links, active states, selected nodes |
| Background (base) | Off-White | `#F7F8FA` | Main content background |
| Background (panel) | Cool Gray | `#EDEFF3` | Cards, side panels |
| Text (primary) | Charcoal | `#1F2328` | Body text |
| Text (secondary) | Slate Gray | `#5A6472` | Metadata, timestamps, captions |
| Success / High Confidence | Muted Green | `#3E8E5A` | High-confidence badges, confirmed matches |
| Caution / Medium Confidence | Amber | `#C98A2C` | Medium-confidence badges, review-needed states |
| Alert / Low Confidence or Risk | Muted Red | `#B84A4A` | Low-confidence flags, high-risk pattern alerts (never bright red — reduces alarm fatigue) |
| Graph — Person node | Indigo `#3B4C8C` | | |
| Graph — Organization node | Teal `#2E7D6B` | | |
| Graph — Location node | Amber `#B5842A` | | |
| Graph — Vehicle node | Slate `#5A6472` | | |
| Graph — Financial Account node | Plum `#6B4C8C` | | |
| Graph — Event node | Rose Gray `#8C5A5A` | | |
| Edge — direct/confirmed relationship | Solid, Charcoal | `#1F2328` | |
| Edge — inferred/indirect relationship | Dashed, Slate Gray | `#5A6472` | |

Dark mode variant defined for extended night-shift analyst use (inverted neutrals, same accent hues at adjusted luminance for WCAG AA contrast).

---

## 3. Typography

| Use | Typeface | Notes |
|---|---|---|
| UI / body | **Inter** | Excellent legibility at small sizes, strong Latin+numeral support |
| Regional-language text (Hindi, etc.) | **Noto Sans Devanagari** (and Noto Sans variants per language) | Ensures consistent rendering of source-document text and regional-language entity names |
| Data/monospace (IDs, phone numbers, account numbers, timestamps) | **JetBrains Mono** | Fixed-width improves scanability and reduces transcription errors for critical identifiers |
| Headings | Inter (Semibold/Bold) | Clear hierarchy, no decorative display fonts |

**Scale**: 12/14/16/20/24/32px modular scale; body text minimum 14px, never below 12px anywhere (field-use readability).

---

## 4. Layout Principles

- **12-column responsive grid**, collapsing to a single-column stacked layout below 768px (tablet/mobile field use).
- **Persistent left-context / right-detail pattern** on desktop for Search, Network Explorer, and Case Workspace: list/graph on the left or center, detail panel slides in from the right — preserves context, avoids full-page navigation for drill-down.
- **Card-based entity representation** everywhere (search results, relationship lists, pattern results) so the same visual unit is recognizable across the app.
- **Sticky global filter bar** on all list/graph screens — filters never scroll out of view.
- **Confidence badges** as a standardized small pill component (colored per §2 confidence scale) attached to every AI-derived statement, always paired with a "view source" affordance.
- **Whitespace discipline**: dense investigative data needs breathing room to avoid cognitive overload; minimum 16px padding within cards, 24px between major sections.

---

## 5. Key Screen Wireframe Descriptions

### 5.1 Dashboard (Investigator)
```
[Top Nav: Logo | Search bar | Alerts bell | Profile]
--------------------------------------------------
[Welcome banner: "3 active cases, 2 new alerts"]
[Row: My Cases (card grid, 3-4 across)] [Recent Alerts (list, right rail)]
[Row: Quick Search widget]  [Recently viewed entities (horizontal scroll cards)]
```

### 5.2 Entity Search Results
```
[Sticky filter bar: Entity type chips | Date range | District | Confidence slider]
[Left: Result list — entity cards: name/ID, type icon, confidence pill, #source records, "Explore Network" button]
[Right (on selection): Preview panel — mini entity summary before full navigation]
```

### 5.3 Network Visualization
```
[Top toolbar: Expand | Collapse | Find Path | Run Pattern Detection | Timeline scrubber | Export]
[Left rail: Filters — relationship type, confidence threshold, date range, "show indirect" toggle]
[Center: Graph canvas — force-directed layout, node size = centrality score, color = entity type]
[Right rail (on node select): Entity summary card + [View Full Profile] + [Add to Case]]
[Bottom: Timeline scrubber bar — drag to filter graph by date range, animates relationship formation over time]
```

### 5.4 Pattern Detection Results
```
[Sticky filter bar: Pattern type | Confidence | Date detected | Case linkage]
[List of pattern cards: pattern type icon, short description, confidence pill, # entities involved, [Confirm] [Dismiss] [Escalate] inline actions]
[Selecting a card opens Pattern Detail: explanation panel (feature breakdown) + embedded mini sub-graph + source citation list]
```

### 5.5 Investigator Workspace (Case Detail)
```
[Case header: FIR#, jurisdiction, status pill, assigned officers avatars]
[Tab bar: Overview | Network Map | Entities | Evidence | Notes | Report Builder | Access Log]
[Overview tab: case summary card, pinned entities (card row), recent activity feed, sensitive-case banner if applicable]
```

---

## 6. Component Library Notes (for design system implementation)

- Confidence pill: `[● High 92%]` / `[● Medium 61%]` / `[● Low 34%]` — color per §2, always includes numeric percentage, never color-only (accessibility).
- Source citation chip: small clickable tag `[FIR#2024/1123]` linking back to original record — appears next to every AI-derived claim in text and reports.
- Sensitive-case banner: persistent, non-dismissible amber banner at top of any restricted case ("This case is access-restricted. Your access is logged.").
- Graph node hover state: shows name + type + confidence without requiring click (reduces navigation fatigue during exploration).

---

## 7. Accessibility & Localization

- WCAG 2.1 AA minimum contrast ratios enforced across the palette above (verified via automated contrast checker in CI for the design system).
- All icons paired with text labels or tooltips — no icon-only critical actions.
- Full UI string externalization for translation (English/Hindi at launch); RTL not required for initial language set but layout uses logical CSS properties to allow future extension.
- Keyboard navigability for all core flows (search, entity detail, report builder) to support accessibility compliance and power-user efficiency.
