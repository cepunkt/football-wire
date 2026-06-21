# Acknowledgements

football-wire uses open data from the following sources:

## openfootball/worldcup.json
- **Repository:** https://github.com/openfootball/worldcup.json
- **Maintainer:** Gerald Bauer (@geraldb)
- **License:** CC0 1.0 Universal (Public Domain)
- **Data:** WC2026 teams, groups, squads (with clubs), stadiums
- **Note:** Upstream source at https://github.com/openfootball/worldcup

## Wikimedia Commons
- **Source:** https://commons.wikimedia.org
- **Data:** Team kit/jersey schematics (PNG)
- **License:** Various (check individual files)

## FIFA Public API
- **Source:** https://api.fifa.com/api/v3/
- **Data:** Live match data, timelines, schedules
- **Access:** Public API, no authentication required
- **Note:** Data quality varies significantly. See docs/api-fifa.md for
  reverse-engineered documentation and known issues.

## ESPN Scoreboard API
- **Source:** https://site.api.espn.com/apis/site/v2/sports/soccer/
- **Data:** Live match statistics (possession, shots on target, key passes)
- **Access:** Public endpoint, no authentication required
- **Note:** Optional integration. Provides the key stats (especially
  possession) that the FIFA API does not expose in its free tier.
  Non-commercial, low-frequency use. See docs/espn-endpoint.md.

## The Fjelstul World Cup Database
- **Repository:** https://github.com/jfjelstul/worldcup
- **Author:** Joshua C. Fjelstul, Ph.D.
- **Copyright:** (c) 2023 Joshua C. Fjelstul, Ph.D.
- **License:** CC-BY-SA 4.0
- **Data:** Historical World Cup data 1930-2022
- **Note:** Not bundled in this repository. Download separately from the source.
  ShareAlike license requires derivative works to carry CC-BY-SA 4.0.
