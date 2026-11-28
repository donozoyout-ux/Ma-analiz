# Stitch preview review

The local ZIP `stitch_ma_analizi_aray_z_tasar_m.zip` contains twelve reference HTML files. Its match scores, predictions and odds are mock data and are not used as application data. The preview screenshots are generated with explicitly synthetic intercepted API fixtures by `npm run test:stitch-browser`; the runner saves six views at 390px and 1440px. The additional 360px and 1280px checks cover layout and navigation.

The following is a structural comparison of each reference HTML with the rendered preview; it is not a pixel-diff or a claim that reference HTML was rendered under identical font/network conditions.

| View | Desktop | Mobile |
| --- | --- | --- |
| Overview | Dark shell, sidebar and data cards present; reference card order and density differ. | Four-item bottom navigation and stacked cards present; reference is more compact. |
| Match center | Filters and source table present; reference layout and fixture presentation differ. | Filters and stacked source rows present; reference uses a more compact card arrangement. |
| AI predictions | Data table and explicit evidence warning present; reference uses prediction cards. | Scrollable records rather than the reference's cards; further responsive work needed. |
| Statistics | Team and performance cards present; chart and layout differ from reference. | Cards stack; team controls and chart differ from reference. |
| Archive | Filters and history records present; reference table and density differ. | Filter stack and history card present; reference list is more compact. |
| Match detail | Empty state until a real ID is selected; reference shows a mock match and signals. | Same safe empty state; selected fixture opens a dark, source-aware detail. |

Synthetic screenshot contents must never be presented as real results. Full reference rendering, side-by-side visual comparison, accessibility audit with assistive technology and design refinement remain open before production review.
