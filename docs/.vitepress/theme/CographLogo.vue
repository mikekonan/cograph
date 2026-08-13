<script setup lang="ts">
/**
 * The application's brand lockup: a monospace `c`, the orbital mark standing in
 * for the `o`, then `graph`. Ported from `CographMark` and its wrapper in
 * `web/src/components/layout/TopBar.tsx` — same viewBox, same dash geometry,
 * same optical nudges (`0.87em` mark, `0.10em` baseline lift, `0.02em` side
 * bearings), so the two headers read as one product.
 *
 * Two deliberate divergences from the app:
 *  - 17px rather than 15px. VitePress's nav is 64px tall against the app's 52px,
 *    and the app's size in this bar reads undersized. The ratio to the bar is
 *    what matches, not the pixel count.
 *  - `--color-accent` is used directly rather than through `currentColor`, since
 *    the surrounding link colour differs here.
 *
 * A `span`, not an `a`: VitePress already wraps this slot in its own
 * `<a class="title" href="/">`, and nesting anchors is invalid HTML — the parser
 * un-nests them, which showed up as a hydration mismatch in the console.
 */
</script>

<template>
  <span class="cograph-logo">
    <span>c</span>
    <svg class="cograph-logo__mark" viewBox="0 0 32 32" aria-hidden="true">
      <circle
        cx="16"
        cy="16"
        r="13"
        fill="none"
        stroke="var(--color-accent)"
        stroke-width="2.2"
        stroke-dasharray="20.23 7"
        stroke-dashoffset="-3.5"
        stroke-linecap="round"
      />
      <circle cx="16" cy="3" r="3.2" fill="var(--color-accent)" />
      <circle cx="4.74" cy="22.5" r="3.2" fill="var(--color-accent)" />
      <circle cx="27.26" cy="22.5" r="3.2" fill="var(--color-accent)" />
    </svg>
    <span>graph</span>
  </span>
</template>

<style scoped>
.cograph-logo {
  display: inline-flex;
  align-items: baseline;
  font-family: var(--vp-font-family-mono);
  font-size: 17px;
  font-weight: 500;
  line-height: 1;
  letter-spacing: -0.01em;
  color: var(--color-fg);
  transition: opacity 0.15s;
}

/* The hover target is VitePress's wrapping anchor, not this element. */
:global(.VPNavBarTitle .title:hover) .cograph-logo {
  opacity: 0.75;
}

.cograph-logo__mark {
  display: inline-block;
  width: 0.87em;
  height: 0.87em;
  margin: 0 0.02em;
  transform: translateY(0.1em);
}
</style>
