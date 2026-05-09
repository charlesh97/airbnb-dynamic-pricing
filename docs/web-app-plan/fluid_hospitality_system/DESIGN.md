---
name: Fluid Hospitality System
colors:
  surface: '#f7f9fb'
  surface-dim: '#d8dadc'
  surface-bright: '#f7f9fb'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f2f4f6'
  surface-container: '#eceef0'
  surface-container-high: '#e6e8ea'
  surface-container-highest: '#e0e3e5'
  on-surface: '#191c1e'
  on-surface-variant: '#404752'
  inverse-surface: '#2d3133'
  inverse-on-surface: '#eff1f3'
  outline: '#707883'
  outline-variant: '#bfc7d4'
  surface-tint: '#0061a4'
  primary: '#0061a4'
  on-primary: '#ffffff'
  primary-container: '#2196f3'
  on-primary-container: '#002c4f'
  inverse-primary: '#9ecaff'
  secondary: '#b81311'
  on-secondary: '#ffffff'
  secondary-container: '#dc3128'
  on-secondary-container: '#fffbff'
  tertiary: '#006876'
  on-tertiary: '#ffffff'
  tertiary-container: '#00a0b5'
  on-tertiary-container: '#003037'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#d1e4ff'
  primary-fixed-dim: '#9ecaff'
  on-primary-fixed: '#001d36'
  on-primary-fixed-variant: '#00497d'
  secondary-fixed: '#ffdad5'
  secondary-fixed-dim: '#ffb4a9'
  on-secondary-fixed: '#410001'
  on-secondary-fixed-variant: '#930005'
  tertiary-fixed: '#a1efff'
  tertiary-fixed-dim: '#44d8f1'
  on-tertiary-fixed: '#001f25'
  on-tertiary-fixed-variant: '#004e59'
  background: '#f7f9fb'
  on-background: '#191c1e'
  surface-variant: '#e0e3e5'
typography:
  display-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.01em
  headline-lg-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  price-display:
    fontFamily: Hanken Grotesk
    fontSize: 20px
    fontWeight: '700'
    lineHeight: 24px
    letterSpacing: 0.02em
  body-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  label-sm:
    fontFamily: Hanken Grotesk
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  base-unit: 8px
  margin-mobile: 20px
  margin-desktop: 64px
  gutter: 24px
  liquid-padding: 32px
---

## Brand & Style

The design system is centered on a "fluid liquid" aesthetic that balances the precision of data-heavy hotel management with an organic, premium user experience. The brand personality is effortless, adaptable, and high-fidelity. It seeks to evoke a sense of calm and clarity, moving away from rigid, boxy structures in favor of soft, undulating forms that suggest movement and ease of use.

The visual style is a sophisticated evolution of modern flat design. It utilizes organic, non-geometric blobs and "liquid" containers to house information, ensuring the UI feels "airy" rather than cluttered. While the shapes are fluid, the data—specifically pricing and availability—is rendered with razor-sharp precision to maintain professional trust.

## Colors

The color strategy uses Material-inspired blues and reds, reimagined for a premium editorial feel. Blue serves as the primary "flow" color, representing navigation, selection, and steady states. Red is used sparingly but vibrantly to highlight premium pricing surges, high-demand dates, or critical alerts.

To maintain the "airy" feel, the background is a very light, desaturated slate rather than pure white, providing a canvas for pure white "liquid" cards to sit upon. Transitions between colors should feel soft, utilizing subtle gradients that mimic the surface of water rather than harsh steps.

## Typography

This design system utilizes **Plus Jakarta Sans** for its approachable yet modern personality, perfect for the travel and hospitality sector. Its slightly rounded terminals complement the fluid shape language. For high-fidelity data—specifically the pricing within the calendar—**Hanken Grotesk** is used to provide a more technical, precise, and professional contrast.

The typography hierarchy prioritizes "Price-Display" tokens, ensuring that even at small sizes, numerical data remains legible and authoritative. Large display headers use tighter letter spacing to create a premium, editorial impact.

## Layout & Spacing

The layout philosophy follows a **fluid grid** model with generous safe areas. Unlike traditional grids that use hard edges, this system employs "Safe Zones" where organic containers can expand or contract. 

- **Desktop:** A 12-column grid with wide margins (64px) to create an "airy" center-focused interface.
- **Calendar Grid:** The calendar does not use hard lines; instead, it uses a soft-cluster layout where dates are individual floating nodes within a larger fluid container.
- **Fluidity:** Padding within components is intentionally oversized (liquid-padding) to ensure the UI never feels cramped, emphasizing the "premium" nature of the experience.

## Elevation & Depth

Depth is achieved through **Ambient Shadows** and **Tonal Layering** rather than traditional borders.

1.  **The Base:** The primary background sits at the lowest level.
2.  **Fluid Containers:** These use extremely soft, elongated shadows (`box-shadow: 0 20px 40px rgba(33, 150, 243, 0.08)`) that are tinted with the primary blue to suggest they are floating on a liquid surface.
3.  **Active Elements:** Elements like selected dates or hover states use a slight vertical lift and a more saturated shadow tint to indicate interaction.
4.  **Glassmorphism:** Navigation bars and sticky headers use a subtle backdrop blur (12px) with 80% opacity to maintain the sense of depth and flow.

## Shapes

The shape language is the core differentiator of this design system. We move away from standard rectangles.

- **Organic Containers:** Use asymmetrical border radii (e.g., `60% 40% 70% 30% / 40% 50% 60% 40%`) for decorative background elements.
- **Functional Components:** Buttons and cards use a base roundedness of `2` (0.5rem) but with "squircle" smoothing to keep them feeling softer than standard CSS border-radius.
- **Calendar Nodes:** Individual dates are circular or highly rounded capsules to maintain the "liquid droplet" motif.

## Components

### Buttons
Primary buttons are pill-shaped with subtle blue-to-light-blue gradients. They lack borders, relying on their vibrant primary color for visibility. On tap, they should "squish" slightly (scale: 0.98).

### The Calendar Grid
The calendar is the hero component. It must be borderless. "Available" dates are droplets of Arctic Cyan. "Selected" dates are vibrant Ocean Blue. "High Demand" dates are marked with a small Vibrant Red "liquid" dot or a soft red glow.

### Liquid Cards
Information blocks (hotel details, price breakdowns) are housed in pure white cards with soft, wide shadows. The top-left and bottom-right corners should have slightly larger radii than the other two corners to mimic an organic form.

### Pricing Chips
High-fidelity pricing data is presented in high-contrast chips using Hanken Grotesk. These chips should have a slight glassmorphic background when placed over imagery to ensure the "airy" feel is maintained.

### Inputs
Search and date-range inputs are elongated capsules. Focus states are indicated by a soft blue glow that radiates from the element, mimicking a ripple effect.