
# Gitea–ESPHome Sync
This repository serves two purposes: it is both a tutorial for setting up bidirectional synchronization between ESPHome Device Builder and Gitea, and the Home Assistant Add-on used to implement that synchronization.

The setup automatically synchronizes the ESPHome configuration files between your Home Assistant OS server and a Gitea repository. This allows you to edit your ESPHome configurations from either the ESPHome Device Builder Web UI or directly on Gitea, with changes automatically propagated to the other side.

## Setup
### Prerequisites
This tutorial assumes that you have:
-   A Home Assistant OS server running the **ESPHome Device Builder** add-on (e.g. on a Raspberry Pi).
-   A running **Gitea** instance that is accessible from your Home Assistant server. 
-   SSH access to your Home Assistant OS server.

### Home Assistant OS → Gitea  Synchronization
1.  Open the ESPHome Device Builder Web UI and enable **Save version history** in its settings. This initializes a Git repository in `/config/esphome` on your Home Assistant OS server.
2.  SSH into your Home Assistant OS server and navigate to `/config/esphome`.
    Check which default branch the repository uses (`main` or `master`).
3.  Create a new repo on your Gitea server with the same default branch as the local repo. Note down the repository URL.    
4.  Create a dedicated Gitea user account for ESPHome Device Builder. Note down its username and password.    
5.  Add your Gitea repository as the `origin` remote:
    ```
    git remote add origin https://[username]:[password]@[gitea-repository-url].git
    ```
6.  Configure a Git `post-commit` hook to automatically push every new commit to Gitea:
    ```
    cat > .git/hooks/post-commit <<'EOF'
    #!/bin/sh
    git push
    EOF
    
    chmod +x .git/hooks/post-commit
    ```
7.  Perform the initial push manually to establish the upstream branch:
    ```
    git push -u origin [branch]
    ```
    Verify that your YAMLs are now visible in the Gitea repository.
    
At this point, changes committed on Home Assistant will automatically be pushed to Gitea.

### Gitea → Home Assistant OS Synchronization
8.  Open your repository in Gitea and navigate to Settings > Webhooks > Add Webhook > Gitea.
    Configure the webhook as follows:
    
    |Option|Value|
    |--|--|
    |Target URL|`http://[HAOS IP or hostname]:[port]`|
    |HTTP Method|POST|
    |Secret|A strong secret|
    |Trigger On|Push Events|

    The port must match the port configured for the Home Assistant Add-on (default: `5000`).
    You can generate a suitable webhook secret using `openssl rand -base64 32`.    
9.  Clone this repository into the `/addons` directory on your Home Assistant OS server:
    ```
    cd /addons
    git clone [repository-url]
    ```
10.  Install the add-on through the Home Assistant Web UI. (See [Home Assistant documentation on local add-ons](https://developers.home-assistant.io/docs/add-ons/tutorial/) for instructions).
    Configure the add-on using the following options:

|Option|Description|
|--|--|
|secret|The webhook secret configured in Gitea|
|target_branch|The full Git branch reference to synchronize, e.g. `refs/heads/main`|
|port|The HTTP listener port. Defaults to `5000`|
