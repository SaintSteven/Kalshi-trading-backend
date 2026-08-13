V2.6.8 MOBILE UPLOAD

Upload the contents of this ZIP to the existing Kalshi-trading-backend repository just like prior backend releases.

IMPORTANT: After GitHub/Render deploys this code, the Lab will show that external checkpointing is NOT configured until you add the Render secret GITHUB_CHECKPOINT_TOKEN.

The checkpoint code automatically creates/uses a separate branch named backtest-checkpoints, so checkpoint commits do not go to the deployed main branch.

Required Render secret:
GITHUB_CHECKPOINT_TOKEN=<your fine-grained GitHub token>

Default repo used by this build:
SaintSteven/Kalshi-trading-backend

Do not put the token in GitHub files or Vercel/frontend settings.
