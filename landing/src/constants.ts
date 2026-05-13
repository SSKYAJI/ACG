/** Replace placeholders before publishing. Every external URL used on the site should live here. */
export const LINKS = {
  demoVideoWatch: "https://youtu.be/dQw4w9WgXcQ",
  docsHome: "https://example.com/acg-docs",
  githubRepo: "https://github.com/example/acg",

  linkedin: {
    prajit: "https://www.linkedin.com/in/prajit-placeholder",
    shashank: "https://www.linkedin.com/in/shashank-placeholder",
  },
  mailto: {
    prajit: "mailto:prajit@example.com",
    shashank: "mailto:shashank@example.com",
  },

  demoVideoEmbed: "https://www.youtube.com/embed/dQw4w9WgXcQ",
} as const;

/** Fixed assumption for scoped vs naive token math (no slider). */
export const CALCULATOR_FIXED_REDUCTION = 0.38;

/** Baseline planner tokens per task when only the task count slider is shown. */
export const CALCULATOR_FIXED_TOKENS_PER_TASK = 2800;
