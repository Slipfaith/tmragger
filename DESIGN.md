# Design System Specification: Technical Precision & Editorial Utility

## 1. Overview & Creative North Star
**The Creative North Star: "The Precision Architect"**

For a technical tool like TMX Repair, the interface must move beyond mere "utility." We are shifting away from the cluttered, line-heavy aesthetic of legacy engineering software toward a "High-End Editorial" experience. The goal is to treat technical data with the same reverence a gallery treats fine art. 

This design system avoids the "template" look by rejecting traditional structural dividers (lines and borders) in favor of **Tonal Sculpting**. By using varying weights of `manrope` for headlines and `inter` for data, we create an authoritative hierarchy. The layout is intentionally spacious, using "negative space as a structural element" to reduce the cognitive load of complex repair operations.

---

## 2. Colors & Surface Philosophy
The palette is a sophisticated blend of cool slates and clinical whites, anchored by a deep, intelligent blue (`primary: #056687`).

### The "No-Line" Rule
**Explicit Instruction:** Designers are prohibited from using 1px solid borders to define sections. Instead, boundaries must be established through background color shifts.
- To separate a sidebar from a main content area, transition from `surface` (#f8f9fa) to `surface-container-low` (#f1f4f6).
- Use `surface-container-highest` (#dbe4e7) for small, high-density utility panels to make them feel "recessed" into the UI.

### Surface Hierarchy & Nesting
Think of the UI as a physical stack of premium cardstock.
*   **Base Layer:** `surface` (#f8f9fa) – The infinite canvas.
*   **Section Layer:** `surface-container-low` (#f1f4f6) – For broad categorization.
*   **Action Layer (Cards):** `surface-container-lowest` (#ffffff) – Used for primary interactive modules to make them "pop" against the slightly greyed background.

### Signature Textures
To avoid a flat, "cheap" feel, use a subtle linear gradient on primary CTAs: 
*   **Direction:** 135deg
*   **From:** `primary` (#056687) 
*   **To:** `primary_dim` (#005977)
This adds a microscopic "sheen" that suggests high-end hardware without resorting to dated glossy effects.

---

## 3. Typography: The Editorial Voice
We utilize a dual-typeface system to balance technical clarity with professional authority.

*   **Display & Headlines (Manrope):** Chosen for its geometric precision and modern "tech-luxury" feel. 
    *   *Usage:* Use `headline-lg` for dashboard titles to establish a strong, confident anchor.
*   **Body & Labels (Inter):** The workhorse for readability.
    *   *Usage:* Use `body-md` for all technical descriptions. For log outputs, use a monospaced variant of Inter or a dedicated mono font to ensure character alignment in repair data.

**Hierarchy Tip:** Always pair a `label-md` (All Caps, 0.05em tracking) with `title-sm` for section headers within cards to create a "Technical Dossier" look.

---

## 4. Elevation & Depth
Depth is achieved through **Tonal Layering**, not structural shadows.

*   **The Layering Principle:** A `surface-container-lowest` card sitting on a `surface-container-low` background creates a natural lift. This is our primary method of elevation.
*   **Ambient Shadows:** When a modal or "floating" menu is required, use a shadow with a 24px blur and 4% opacity, using `on-surface` (#2b3437) as the shadow color. It should feel like a soft glow of light, not a dark drop shadow.
*   **The "Ghost Border" Fallback:** If a technical constraint requires a border (e.g., high-contrast accessibility mode), use `outline-variant` (#abb3b7) at **15% opacity**.
*   **Glassmorphism:** Use `surface_container_lowest` at 80% opacity with a `20px backdrop-filter: blur()` for floating status bars or headers. This ensures the technical data "scrolls under" the navigation, maintaining a sense of spatial depth.

---

## 5. Components

### Cards & Layout
*   **Rule:** Zero dividers. Use vertical spacing (e.g., 32px) and background color shifts to separate "Header," "Content," and "Footer" within a card.
*   **Style:** `rounded-lg` (0.5rem) for main cards; `rounded-md` (0.375rem) for nested elements.

### Buttons
*   **Primary:** Gradient of `primary` to `primary_dim`. Text: `on_primary`. Shape: `rounded-md`.
*   **Secondary:** `secondary_container` (#cbe7f5) background with `on_secondary_container` (#3c5561) text. No border.
*   **Tertiary:** Transparent background. Text: `primary`. Use only for low-priority actions like "Cancel" or "Learn More."

### Input Fields
*   **State:** Default background should be `surface_container_high` (#e3e9ec) to look "inset." 
*   **Focus:** Transition background to `surface_container_lowest` (#ffffff) and apply a 2px "Ghost Border" of `primary` at 40% opacity.

### The Technical Log (Unique Component)
*   **Container:** `inverse_surface` (#0c0f10).
*   **Typography:** Monospaced, `label-md`. 
*   **Styling:** Use `inverse_on_surface` (#9b9d9e) for timestamp data and `primary_fixed` (#c1e8ff) for "SUCCESS" flags to ensure high legibility against the dark void.

---

## 6. Do’s and Don’ts

### Do:
*   **Do** use asymmetrical margins. If a card is 400px wide, try a 48px left margin and a 32px right margin for a more "designed" editorial feel.
*   **Do** rely on font weight (Bold vs. Regular) rather than color to differentiate information hierarchy.
*   **Do** use `surface-dim` for inactive or "disabled" states rather than just lowering the opacity of the element.

### Don't:
*   **Don't** use 100% black (#000000) for text. Always use `on-surface` (#2b3437) to maintain a soft, premium contrast.
*   **Don't** use icons as the sole communicator. For a technical tool, a clear `label-sm` next to an icon prevents catastrophic user error.
*   **Don't** use "Card Shadows" on every element. If everything is elevated, nothing is important. Keep the UI flat and use elevation only for the most critical user actions.