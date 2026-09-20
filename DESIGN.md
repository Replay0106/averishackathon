# DESIGN.md — NavisAI Design System & Visual Specification

## 1. Aesthetic Archetype: Industrial Precision Cockpit
NavisAI adopts an **"Industrial Logistics Luxury"** design language — combining the razor-sharp tactile density of high-frequency financial terminals with the polished minimalism of Linear and Apple Pro interfaces.

### Core Visual Tenets:
1. **Double-Bezel Architecture (Doppelrand)**: Key metrics, redline comparison panels, and alert banners use nested concentric enclosures (an outer hairline shell wrapping an inner core with subtle inner ring highlight).
2. **Tactile Information Hierarchy**: Microscopic uppercase tracking badges for status, high-contrast monospace for identifiers and numerical values, and clean geometric sans for legal entity names.
3. **Calibrated Color Semantics**: Color is never decorative; every tint conveys operational state and regulatory status.

---

## 2. Color Palette & Design Tokens
```css
:root {
    /* Brand Foundation */
    --averis-orange: #F37021;          /* Primary Accent & Hero Elements */
    --averis-orange-deep: #EE5D24;     /* Active States & Focus Rings */
    --averis-orange-glow: rgba(243, 112, 33, 0.15);

    /* Backgrounds & Canvas */
    --canvas-base: #0B1120;            /* Deep Obsidian Command Canvas */
    --canvas-card: #0F172A;            /* Primary Surface Container */
    --canvas-subcard: #1E293B;         /* Nested Inner Core Surface */
    --canvas-border: #334155;          /* Machined Hairline Border */
    --canvas-border-subtle: #1E293B;

    /* Text & Content */
    --text-primary: #F8FAFC;           /* 100% Primary Headings & Data */
    --text-secondary: #94A3B8;         /* 70% Secondary Labels & Metadata */
    --text-muted: #64748B;             /* 40% De-emphasized Captioning */

    /* Semantic State Tokens */
    --state-success: #10B981;          /* Bank Ready / Match (Emerald) */
    --state-success-bg: rgba(16, 185, 129, 0.10);
    --state-danger: #EF4444;           /* Critical Discrepancy (Crimson) */
    --state-danger-bg: rgba(239, 68, 68, 0.12);
    --state-warning: #F59E0B;          /* Human Review Needed / Warning (Amber) */
    --state-warning-bg: rgba(245, 158, 11, 0.12);
    --state-info: #38BDF8;             /* Vision / AI Processing (Cyan/Sky) */
    --state-info-bg: rgba(56, 189, 248, 0.10);
}
```

---

## 3. Component Hierarchy & Micro-Interactions

### A. Double-Bezel Metric Cards
- **Outer Shell**: `border: 1px solid var(--canvas-border); border-radius: 14px; padding: 2px; background: var(--canvas-card);`
- **Inner Core**: `border-radius: 12px; padding: 12px 16px; background: var(--canvas-subcard); box-shadow: inset 0 1px 1px rgba(255,255,255,0.06);`

### B. Visual Redline Diff (SI vs. BL)
- **Original / Draft Error**: Enclosed in a pale red badge with strikethrough styling (`text-decoration: line-through; color: #F87171; background: rgba(239,68,68,0.15);`)
- **Verified Target (SI)**: Enclosed in a pale emerald badge with bold verification glyph (`color: #34D399; background: rgba(16,185,129,0.15); font-weight: 600;`)

### C. Multimodal Scanned Image Quality Banner
- Rendered whenever an image-only or scanned PDF is analyzed:
  - Displays: `[📷 Scanned Document Mode]` badge.
  - Quality score gauge: `98% Legible | OCR Confidence: High`.
  - Visual status pill: `PASS (Clear Table Grid)` or `ESCALATE (Unreadable Blur)`.

### D. Floating Status Pills
- Microscopic uppercase tracking badges: `font-size: 0.72rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; border-radius: 9999px; padding: 3px 10px;`

---

## 4. Strict Anti-Patterns (Banned Design Clichés)
1. **NO Generic AI Purple Gradients**: No generic `#8B5CF6` to `#3B82F6` gradients. Use only Averis Brand Orange, Emerald, Crimson, and Obsidian Slate.
2. **NO Naked Icon Buttons**: Action buttons must have legible micro-copy or be wrapped in nested pills.
3. **NO Raw Unformatted JSON Dumps**: All data must be rendered in structured tables, key-value grids, or comparison cards.
4. **NO Harsh Drop Shadows**: Use subtle inner ambient rings (`inset 0 1px 0 rgba(255,255,255,0.08)`) rather than dirty black drop shadows.
