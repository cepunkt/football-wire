# Acknowledgements

football-wire uses open data from the following sources:

## openfootball/worldcup.json
- **Repository:** https://github.com/openfootball/worldcup.json
- **Maintainer:** Gerald Bauer (@geraldb)
- **License:** CC0 1.0 Universal (Public Domain)
- **Data:** WC2026 teams, groups, squads (with clubs), stadiums
- **Note:** Upstream source at https://github.com/openfootball/worldcup

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

