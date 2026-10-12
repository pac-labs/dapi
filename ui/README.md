# Panel styles

The management panel uses official IBM Carbon v11 Sass components, the Gray 100
theme, and a responsive layout in `panel.scss`. Native HTML/JavaScript keeps the
existing same-origin management behavior. No CDN, external fonts, or additional
browser JavaScript framework is needed. Font stacks use installed fonts.

Before running Python from a source checkout, tests, or building a wheel:

```sh
npm ci --ignore-scripts
npm run build:ui
```

Node 24 is a build dependency only. The locked dependencies are compiled into
`looprd/panel_assets/panel.css`; this generated file is excluded from Git and
included in the Python package. The Containerfile and CI perform this build.
Rebuild and restart the local Python server after editing Sass.

Carbon is Apache-2.0 licensed; see the bundled `carbon-LICENSE.txt`. Dependency
versions and their transitive dependencies are pinned in `package-lock.json`.
Update them deliberately and verify desktop/mobile layouts and key workflows.

References: [Carbon vanilla styling](https://carbondesignsystem.com/developing/community-frameworks/vanilla/)
and [Carbon Sass](https://github.com/carbon-design-system/carbon/blob/main/packages/styles/docs/sass.md).
