# AI news watcher

Reads about 100 public AI and tech news feeds every 10 minutes and posts a short "possibly breaking" note to a Slack channel when:

1. **several outlets carry the same AI story**: three or more different websites within three hours (titles grouped by their shared key words; feeds of one website count once; newsletters and analysis blogs don't count), or
2. **an authority acts**: an EU or national institution's feed publishes an AI item with a rule word (ruling, fine, ban, investigation, proposal, guidance...).

Each story alarms once a day at most, and there are at most six alarms a day. No AI model is involved: the watcher is a quick signal between the scheduled news checks of a separate, private scanner, which reads, checks and rates the stories.

- `watcher.py`: the watcher, Python standard library only. `python watcher.py --dry-run` prints what it would send.
- `feeds.json`: the feeds, all public RSS addresses.
- `.github/workflows/watch.yml`: runs it every 10 minutes and keeps the alarms already sent on the `state` branch.

Slack: the bot token is the repository secret `SLACK_BOT_TOKEN`, the channel id the repository variable `SLACK_CHANNEL`. Without them it only prints.
