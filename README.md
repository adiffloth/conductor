# Project Brief: Secure Hybrid Family AI Assistant

## Project Description
The goal of this project is to build a secure, private, and highly accessible AI family assistant using **Hermes Agent**. The assistant will act as a centralized hub for a single household, allowing multiple family members to coordinate daily logistics, manage shared schedules, and automate home routines. 

To ensure maximum availability and natural interaction, the assistant runs concurrently across multiple communication channels: **Telegram** (for rich messaging and group chats), **SMS** (for simple, app-free access, pending carrier registration — see iMessage below), **iMessage** (for native, app-free access on Apple devices, serving as the interim low-friction text channel while SMS onboarding completes), and **Direct Voice Calls** (for hands-free, real-time spoken conversations). 

Security, low latency, and performance are critical priorities. The assistant utilizes a hybrid intelligence model, keeping private household data and routine executions local, while leveraging cloud models for complex reasoning. To protect the host system, the runtime core must be strictly sandboxed from personal user files while maintaining access to high-performance local hardware acceleration.

---

## Example Usage Scenarios

*   **Scenario 1: Shared Household Logistics (Telegram Group)**  
    A parent tags the bot in the family group chat: *"@HermesBot add milk and organic eggs to the grocery list, and remind me at 3 PM to pick up Sam from soccer practice."* Hermes parses the request, updates a persistent shared document, sets a background timer, and drops a cleanly formatted markdown confirmation into the chat.

*   **Scenario 2: Hands-Free Voice Coordination (Phone Call)**  
    While driving, a family member dials the dedicated household phone number. Hermes answers the call using an ultra-realistic, low-latency voice. The driver asks, *"Hey, what do we have scheduled for tomorrow afternoon?"* Hermes queries the family calendar and reads out the agenda. The user cuts in mid-sentence (barge-in): *"Actually, add a reminder to call Grandma at 4."* Hermes immediately stops speaking, registers the interruption, saves the reminder, and responds naturally.

*   **Scenario 3: Low-Tech Fallback Access (SMS)**  
    A family member without a smartphone data plan sends a standard text message to the dedicated household number: *"What time is the dentist appointment tomorrow?"* Hermes intercepts the SMS, queries the local calendar database, and texts back an instant, concise reply.
    *(Implementation note: Twilio SMS requires A2P 10DLC carrier registration, in progress. In the meantime, iMessage via Photon serves as the interim text channel for family members on Apple devices — see project_plan.md for the tradeoffs.)*

*   **Scenario 4: Secure Local Execution (Sandboxed Sandbox)**  
    The system administrator asks Hermes to generate a script that aggregates weekly household chore completions. Because the file operations happen inside a restricted environment, the agent executes the task safely without any ability to read or mutate the host system's primary user directories or cloud backup folders.

---

## Technical Stack

The AI coding agent should utilize the following technologies to implement the system:

*   **Host Hardware:** Mac Studio (M4 Max, 64GB Unified Memory).
*   **Local Inference Engine:** **oMLX** running bare-metal on macOS, exposing an OpenAI-compatible API to leverage the Apple Silicon GPU and Metal framework for local open-weights LLMs (e.g., 12B to 32B models).
*   **Agent Core Framework:** **Hermes Agent**, utilizing its native tool-use routing, skill marketplace capabilities, and terminal execution backends.
*   **Security & Isolation:** **Docker** wrapper. The Hermes gateway and execution environment must run inside a containerized sandbox, utilizing internal network rules to communicate back out to the host's oMLX server.
*   **Communication Channels:** 
    *   **Telegram Bot API** (via BotFather framework tokens).
    *   **Twilio Programmable SMS API** (for inbound and outbound text routing) — pending A2P 10DLC carrier registration.
    *   **Photon (Spectrum SDK)** (managed iMessage integration, free tier) — interim/parallel text channel for Apple-device family members while SMS registration is pending.
    *   **Twilio Programmable Voice & ElevenLabs (ElevenAgents)** (orchestrated webhooks for full-duplex, real-time conversational streaming, speech-to-text, ultra-realistic voice synthesis, and audio barge-in support).
*   **Network Ingress:** **Ngrok** to create a secure public tunnel endpoint that routes incoming Twilio webhooks (both SMS and Voice/ElevenLabs media streams) safely directly to the sandboxed container port. (Photon does not require ngrok — it uses a persistent outbound connection, no public webhook.)
