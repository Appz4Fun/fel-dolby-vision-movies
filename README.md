# FEL Dolby Vision Movies

### 👉 [Browse the full catalog on GitHub Pages](https://appz4fun.github.io/fel-dolby-vision-movies/)

A continuously updated catalog of 4K UHD Blu-ray releases that ship with a
Dolby Vision **Profile 7 FEL** (Full Enhancement Layer) video track.

A daily automated pipeline scrapes physical-media forums and curated lists,
verifies each release against Profile 7 FEL evidence, enriches it with TMDB
and blu-ray.com metadata, and opens a data-refresh pull request when new
releases are found. After that PR is manually merged, GitHub Pages renders the
dashboard linked above. The machine-readable data lives in
[`data/releases.json`](data/releases.json).

For manual entry submit your FEL releases to this reddit thread which will be
picked up the next day when this scraper runs
[Master DVP7 FEL - Reddit Thread](https://www.reddit.com/r/CoreElecOS/comments/1j3lgw2/list_of_dolby_vision_p7fel_films/)
