<script setup lang="ts">
/**
 * Puts the application's brand lockup everywhere the site shows the brand as a
 * mark rather than as a word in a sentence: the header, and the home hero.
 *
 * VitePress renders either `themeConfig.logo` (an image) or `siteTitle` (text),
 * and the lockup is neither — it is text with the mark set inside it as a glyph.
 * `siteTitle: false` removes the stock title and these slots supply the real one.
 *
 * The hero's `name` field is likewise unset in index.md: the lockup replaces it,
 * so the page leads with the wordmark and `hero.text` becomes the headline.
 */
import DefaultTheme from "vitepress/theme-without-fonts";
import CographLogo from "./CographLogo.vue";

const { Layout } = DefaultTheme;
</script>

<template>
  <Layout>
    <template #nav-bar-title-before>
      <CographLogo />
    </template>

    <template #home-hero-info-before>
      <!-- Sized as a wordmark above the headline, not as the headline. The app
           only ever shows the lockup small, so a hero-scale version would be
           inventing a treatment the product does not have. -->
      <CographLogo class="hero-logo" size="clamp(30px, 4.6vw, 44px)" />
    </template>
  </Layout>
</template>

<style scoped>
.hero-logo {
  display: flex;
  margin-bottom: 18px;
}

@media (min-width: 960px) {
  .hero-logo {
    margin-bottom: 22px;
  }
}
</style>
