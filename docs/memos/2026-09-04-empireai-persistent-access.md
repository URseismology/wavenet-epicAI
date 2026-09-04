Subject: WaveNet-EpicAI — Empire AI GPU Access for Your Own Claude Code Sessions

Hi Tejwaswini and Chris,

We now have working SSH access to Empire AI (Alpha/Grace — H100/H200 GPUs,
our target hardware for the PyTorch U-Net training once the CPS datasets
are ready). This memo explains how the access works and walks you through
setting up the same thing under your own axon-1 account, so your own
Claude Code sessions can reach Empire AI directly too.

Full technical details: docs/HANDOFF.md §4.4. This memo is the "how do I
get this working for me" version.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHY THIS NEEDS A MANUAL STEP — READ THIS FIRST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Empire AI requires password + 2FA (authenticator app code) on every login.
Claude Code cannot complete a 2FA challenge — there's no way around that,
and we shouldn't try to work around it. So Claude can never be the one to
*open* the first connection.

What it CAN do is reuse a connection you already opened. SSH has a
feature called ControlMaster/ControlPersist: the first login (yours,
with 2FA) becomes a "master" connection, and every subsequent `ssh`/`scp`
that matches the same config entry reuses that same authenticated socket
— no new password, no new 2FA code — for as long as the socket stays
alive (we've set it to 12 hours of inactivity).

This is the exact same pattern already in use for Bluehive. If you've
used Bluehive from axon-1 before, this will feel familiar.

Important: the socket is scoped to YOUR axon-1 account's home directory.
My having a live connection does not give your Claude Code session
access — you each need to do this setup once, under your own account.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SETUP — DO THIS ONCE PER ACCOUNT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

You'll need your own Empire AI account first (password set, 2FA app
enrolled). If you don't have one yet, let me know — I'll help you get
Early Adopter Access set up before you do the steps below.

□ STEP 1 — Add this to ~/.ssh/config in YOUR axon-1 home directory

  (SSH into axon-1 as yourself first: ssh wavenet-senior@10.17.6.243 or
  ssh wavenet-junior@10.17.6.243, then edit ~/.ssh/config there.)

    Host empireai alpha1.empireai.edu alpha.empire-ai.org alpha1.empire-ai.org
      HostName alpha1.empireai.edu
      User <your-empireai-username>
      ControlMaster auto
      ControlPath ~/.ssh/cm-%r@%h:%p
      ControlPersist 12h
      ServerAliveInterval 30
      ServerAliveCountMax 6

  Replace <your-empireai-username> with your own Empire AI login — this
  is very likely NOT the same as your axon-1 username. Ask me if unsure.

□ STEP 2 — Open the master connection yourself, in your own terminal

    ssh empireai

  Enter your password, then your 6-digit authenticator code when
  prompted. You should land on a welcome banner from the Alpha/Grace
  cluster. Leave that session open, or just exit it — the connection
  stays alive either way, for 12 hours of inactivity.

  Known quirk: the very first connection attempt sometimes fails with
  "Connection refused" even with everything configured correctly, then
  succeeds immediately on retry. This is flakiness on Empire AI's side
  (their support KB describes it), not a problem with your setup. Just
  try again once before assuming something's wrong.

□ STEP 3 — Confirm the socket is live

    ssh -O check empireai

  Expected: "Master running (pid=...)"

□ STEP 4 — Start (or continue) your Claude Code session on axon-1

  From this point on, for the next 12 hours of inactivity, Claude Code
  running under your account can run `ssh empireai '...'` and
  `scp ... empireai:...` directly — no prompts, no copy-pasting output
  back and forth. Ask it to check GPU availability as a first test:

    ssh empireai "nvidia-smi"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KEEPING IT ALIVE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ✓ The socket dies after 12 hours with no traffic through it — just
    repeat Steps 2–3 to refresh it. There's no way to make this fully
    unattended; a human has to be the one who does the 2FA step.
  ✓ If Claude Code reports an Empire AI command failing with a
    connection error partway through a session, the socket has likely
    expired — just log in again.
  ✓ For a long unattended job (training run that will outlive 12
    hours), don't rely on the live socket to fetch results afterward.
    Have the job write its output somewhere both sides can already
    reach without 2FA (e.g. push to repovibranium at the end of the
    job), the same pattern we already use for Bluehive.

  ✗ Don't share your ~/.ssh/config ControlPath or try to point two
    accounts at the same socket file — each account's socket is tied
    to that account's authenticated session.
  ✗ Don't script around the 2FA step (no auto-typing codes, no storing
    the authenticator secret anywhere) — that defeats the point of 2FA
    and Empire AI's own security monitoring will flag it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Questions or if Step 1 (getting an Empire AI account) hasn't happened
yet for you — come find me or ask in the Claude Teams Project.

— Tolu
