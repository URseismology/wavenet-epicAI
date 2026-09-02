Subject: WaveNet-EpicAI — New Lab AI Workflow: Your Setup & First Tasks

Hi Tejwaswini and Chris,

I've migrated WaveNet-EpicAI to a proper team AI workflow. This email
covers everything you need to get set up, understand the architecture,
and complete your first tasks. Please read it fully before doing anything
— it's shorter than it looks.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
THE BIG PICTURE — HOW THIS WORKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

I've set up three tools that work together:

  1. axon-1 (10.17.6.243) — your coding hub
     A dedicated lab machine you both SSH into with your own accounts.
     All code editing, git commits, and Claude Code sessions happen here.
     I've already configured passwordless access to terravibranium
     (compute) and repovibranium (NAS backup) for both of you.

  2. Claude Teams (claude.ai) — our shared AI research assistant
     A shared project workspace where the three of us collaborate with
     Claude. Every conversation inside the WaveNet-EpicAI Project
     automatically loads the full project context — infrastructure,
     rules, dataset status, your role. You don't need to re-explain
     the project at the start of every conversation.

  3. GitHub (URseismology/wavenet-epicAI) — the single source of truth
     All code, docs, and project rules live here. Claude Teams holds a
     snapshot of the key docs. Claude Code reads them live from the repo.
     When anything is unclear, GitHub is correct.

How they relate:

  GitHub  →  axon-1          (your working clone — always git pull first)
  GitHub  →  Claude Teams    (snapshot of key docs, I update at milestones)
  axon-1  →  terravibranium  (SSH, overnight simulation jobs)
  axon-1  →  repovibranium   (SSH, backup verification)

  Important: I need all commits to come from axon-1 only.
  Never commit directly from terravibranium or repovibranium.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR ACCOUNTS AND ROLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Tejwaswini — wavenet-senior
  axon-1 login:    ssh wavenet-senior@10.17.6.243
  I need you on:   overnight simulation runs, Bluehive SLURM setup,
                   ML pipeline development, HDF5 schema migration
  GitHub:          I've sent you a collaborator invite — please accept it
  Claude Teams:    I've sent you a workspace invite — please accept it

Chris — wavenet-junior
  axon-1 login:    ssh wavenet-junior@10.17.6.243
  I need you on:   log monitoring and summarization, HDF5 verification,
                   chrisScripts/ pipeline maintenance, documentation
  GitHub:          I've sent you a collaborator invite — please accept it
  Claude Teams:    I've sent you a workspace invite — please accept it

I'll share your axon-1 initial passwords with each of you separately.
Please change your password the moment you first log in:
  passwd

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SETUP CHECKLIST — PLEASE COMPLETE BY FRIDAY SEPTEMBER 4
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

□ STEP 1 — SSH into axon-1

  Add this to ~/.ssh/config on your laptop:

  Tejwaswini:
    Host axon1
      HostName 10.17.6.243
      User wavenet-senior

  Chris:
    Host axon1
      HostName 10.17.6.243
      User wavenet-junior

  Then connect and change your password:
    ssh axon1
    passwd

□ STEP 2 — Set up VS Code Remote-SSH

  Install VS Code on your laptop if you don't have it:
    https://code.visualstudio.com

  Install the "Remote - SSH" extension from the Extensions panel.

  Connect to axon-1:
    Ctrl/Cmd+Shift+P → "Remote-SSH: Connect to Host" → axon1

  Open the repo:
    File → Open Folder → /Users/wavenet-senior/wavenet-epicAI
                      OR /Users/wavenet-junior/wavenet-epicAI

  You'll have a full VS Code environment running on axon-1 under your
  own account, completely isolated from each other.

□ STEP 3 — Read CLAUDE.md before touching anything

  In your VS Code terminal on axon-1:
    cat ~/wavenet-epicAI/CLAUDE.md

  This is the rule file I've written that governs all AI sessions on
  this project. Claude Code reads it automatically every session.
  Please know what's in it — especially what requires my sign-off.

□ STEP 4 — Verify your git identity and repo state

    cd ~/wavenet-epicAI
    git pull
    git config user.name
    git config user.email
    git log --oneline -3

  Confirm your name appears and the repo is current.
  If your git identity isn't set yet:
    git config user.name "Your Name"
    git config user.email "your@email.com"

□ STEP 5 — Test your SSH access to downstream machines

  Both of these should connect without a password prompt:

    ssh terravibranium "echo OK && uptime"
    ssh repovibranium "echo OK && ls /volume1/ADAMA-Shared/traindatawavenet/"

  If either fails, please let me know before continuing.

□ STEP 6 — Authenticate Claude Code

  In your VS Code terminal on axon-1:
    source ~/.nvm/nvm.sh
    claude

  The first run opens a browser tab — sign in with your Anthropic
  account. Please use the same account you accepted my Claude Teams
  invite with. After auth, confirm it's working:
    claude --version

□ STEP 7 — Accept my Claude Teams invite and test the Project

  Accept the workspace invite from claude.ai in your email.
  Open the WaveNet-EpicAI Project and start a conversation:

    "What is the current dataset status and what are my next priorities?"

  Claude should answer correctly from the project context without you
  explaining anything. If it does, your setup is working.

□ STEP 8 — Run your verification test and report back

  Tejwaswini — verify the existing dataset on terravibranium:

    ssh terravibranium \
      "/home/tolugboj/miniconda/envs/wavenet/bin/python3 -c \
      \"import h5py; f=h5py.File('/RAID6/wavenet_output/wavenetv2_dataset_10k_full.h5','r'); \
      n=len(list(f.get('simulations',{}).keys())); f.close(); print(f'{n} models verified')\""

    Expected: 10000 models verified

  Chris — verify the NAS backup on repovibranium:

    ssh repovibranium \
      "ls -lh /volume1/ADAMA-Shared/traindatawavenet/"

    Expected: wavenetv2_dataset_10k_full.h5 present at ~11 GB

  Please post your results in the WaveNet-EpicAI Claude Teams Project
  by Friday September 4.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHERE THE PROJECT STANDS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Here's what we've accomplished and what I need from you next:

Completed:
  ✅ Physics simulator (wvsim_main.py) — 7 physics bugs fixed,
     production-ready
  ✅ 10,000-model dataset at sep_km=127.0 km — 0 errors, 16.65 hrs
  ✅ Dataset backed up to repovibranium (11 GB)
  ✅ axon-1 hub fully provisioned, all accounts active
  ✅ GitHub docs clean and consistent with our new infrastructure

This week after setup:
  ⏳ Tejwaswini: I'll confirm a sep_km value for you to run tonight
     on terravibranium — please don't launch without my go-ahead
  ⏳ Chris: monitor that run's log and produce a verification report
     when it completes using verify_main.py

Coming up:
  🔲 Bluehive multi-separation array (100 sep_km values, 50–297.5 km)
  🔲 ML pipeline: adapt dataloader and U-Net for the new CPS HDF5 schema
  🔲 PyTorch U-Net training on terravibranium-gpu or Empire AI

Full project history and technical details are in docs/HANDOFF.md.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KEY RULES — PLEASE READ THESE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ✓ git pull at the start of every session
  ✓ git push when your work is complete
  ✓ Check terravibranium load before scheduling any job:
      ssh terravibranium "uptime"
  ✓ Run intensive jobs overnight only — never during lab hours
  ✓ After each completed run: rsync the HDF5 to repovibranium,
    update HANDOFF.md, post a summary in the Claude Teams Project
  ✓ Open a pull request for any change to wvsim_main.py —
    I'll review and merge it

  ✗ Never commit from terravibranium or repovibranium
  ✗ Never modify wvsim_main.py physics logic without my sign-off
  ✗ Never submit Bluehive jobs without my approval
  ✗ Never delete files in /RAID6/wavenet_output/ without my approval
  ✗ Never use the old Instaseis/MPI pipeline — it's fully superseded

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW TO USE CLAUDE DAY TO DAY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

For coding, simulations, file work, terminal tasks:
  → Claude Code on axon-1
  cd ~/wavenet-epicAI && claude
  It reads CLAUDE.md automatically and knows the full infrastructure.
  Your sessions are private — only your git commits are visible to me.

For research, writing, planning, monitoring summaries:
  → Claude Teams at claude.ai, WaveNet-EpicAI Project
  Full project context loads automatically every conversation.
  I can see all conversations in this Project, so please use it as
  our shared lab notebook — not just a personal assistant.

When something needs my approval:
  → Flag it in the Claude Teams Project or come to me directly.
  Claude Code will also flag escalation points automatically —
  trust it when it does.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Please complete the setup checklist and post your Step 8 results
in the Claude Teams Project by Friday September 4.

Start with the Claude Teams Project if you have questions — Claude
will likely answer from the project context. If not, come to me.

— Tolu
