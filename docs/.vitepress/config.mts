import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'ASSella',
  description: 'Fast, modern Steam game and DLC management for SteamOS and Linux.',
  base: '/',
  cleanUrls: true,
  ignoreDeadLinks: true,
  srcExclude: ['**/DEPENDENCIES.md', '**/OPTIONAL_COMPONENTS.md', '**/THEMES.md'],
  head: [
    ['link', { rel: 'icon', type: 'image/png', href: '/logo.png' }]
  ],
  themeConfig: {
    logo: '/logo.png',
    siteTitle: 'ASSella',
    nav: [
      { text: 'Home', link: '/' },
      { text: 'Installation Guide', link: '/install' },
      { text: 'Arch Repo', link: '/arch-repo' },
      { text: 'Decky Plugin', link: '/decky' },
      {
        text: 'Documentation',
        items: [
          { text: 'Themes', link: '/themes' },
          { text: 'Dependencies', link: '/dependencies' },
          { text: 'Optional Components', link: '/optional-components' }
        ]
      },
      { text: 'Releases', link: 'https://github.com/niwia/ASSella/releases' }
    ],
    sidebar: [
      {
        text: 'Getting Started',
        items: [
          { text: 'Installation Guide', link: '/install' },
          { text: 'Arch / SteamOS Repo', link: '/arch-repo' },
          { text: 'Decky Plugin (Uran)', link: '/decky' }
        ]
      },
      {
        text: 'Reference & Guides',
        items: [
          { text: 'Themes Guide', link: '/themes' },
          { text: 'Dependencies', link: '/dependencies' },
          { text: 'Optional Components', link: '/optional-components' }
        ]
      }
    ],
    socialLinks: [
      { icon: 'github', link: 'https://github.com/niwia/ASSella' }
    ],
    search: {
      provider: 'local'
    },
    footer: {
      message: 'ASSella — Advanced Steam Session & Library Assistant',
      copyright: 'Community-driven open source project'
    }
  }
})
