# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : docs/diagrams/src/backmatter.py
# Purpose : Shared back matter for all PDFs: glossary, bibliography, index terms
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Back matter shared by every PDF: glossary, bibliography, index terms.

Each PDF automatically gets:

- the glossary entries whose term (or one of its search patterns) appears in its body;
- the bibliography references (books in English and in French, standards and papers) for the
  topics it covers;
- an index of the glossary terms and extra terms found in its body, with page numbers.

Search patterns (see ``build.find_pattern``): a pattern containing an uppercase letter is matched
case-sensitively, otherwise case-insensitively (a leading ``=`` forces a case-sensitive match of a
lowercase word, e.g. "=uv"); it must start on a word boundary and end on one, unless it ends with
``*`` (stem: "embedding*" also matches "embeddings").

All strings are rendered into the PDFs, in technical US English. The French books keep their
published French titles.
"""

from __future__ import annotations

# (term, definition, search patterns used for filtering and indexing)
GLOSSARY: list[tuple[str, str, tuple[str, ...]]] = [
    ("802.3af / at / bt", "IEEE Power over Ethernet standards: 15.4 W (af, \"PoE\"), 30 W (at, \"PoE+\"), 60 to 90 W "
     "at the source (bt, \"PoE++\", classes 5 to 8). The PTZ cameras of the project draw up to 51 W and need "
     "802.3bt or the vendor's Hi-PoE injector.", ("802.3bt", "802.3at", "802.3af", "PoE++")),
    ("ACME", "Automatic Certificate Management Environment (RFC 8555): protocol used by certbot to obtain "
     "certificates from Let's Encrypt, with an HTTP-01 or DNS-01 challenge.", ("ACME",)),
    ("AEC", "Acoustic Echo Cancellation: removal of the loudspeaker signal from the microphone signal. Performed "
     "here by the XMOS XVF-3000 chip of the ReSpeaker.", ("AEC", "echo cancellation")),
    ("AGPL-3.0", "GNU Affero General Public License v3: strong copyleft license that also covers use over a "
     "network. License of Ultralytics YOLO.", ("AGPL*",)),
    ("Alloy / Promtail", "Grafana agents that ship log files and the systemd journal to Loki. Grafana has deprecated "
     "Promtail in favor of Alloy.", ("Alloy", "Promtail")),
    ("Anubis", "Proof-of-work reverse proxy that keeps crawlers, AI agents and scrapers away from the web UI "
     "(instance anubis@jarvis on 127.0.0.1:8923).", ("Anubis",)),
    ("API (REST)", "HTTP programming interface in which each resource (person, sighting...) has a URL and is "
     "handled with the GET, POST, PATCH and DELETE verbs.", ("REST", "API")),
    ("Adaptive learning", "Automatic, capped enrichment of a person's gallery with their best captures, when they "
     "are recognized with certainty.", ("adaptive learning", "AdaptiveEnroller")),
    ("ArcFace", "Neural network and loss function (additive angular margin) that turn an aligned face into a "
     "512-dimension vector, compared by cosine similarity.", ("ArcFace",)),
    ("Argon2id", "Password hashing function resistant to GPU attacks (winner of the Password Hashing Competition, "
     "RFC 9106).", ("Argon2*",)),
    ("AVX2", "256-bit vector instruction set of Intel processors since Haswell, used by OpenVINO and ONNX Runtime. "
     "Missing from the Celeron and Pentium parts of that generation.", ("AVX2",)),
    ("BIOS / Setup Utility", "Configuration firmware of the ThinkCentre (F1 at power-on, F12 for the boot menu).",
     ("Setup Utility", "BIOS")),
    ("BOM", "Bill of Materials: exhaustive list of the hardware components, with quantities and references.",
     ("BOM",)),
    ("ByteTrack", "Multi-object tracking algorithm that associates detections from one frame to the next by IoU, in "
     "two passes (high then low confidence), with Kalman filter prediction.", ("ByteTrack",)),
    ("CA (certificate authority)", "Entity that signs certificates. Jarvis either runs a private, name-constrained "
     "\"Jarvis Local CA\" or uses Let's Encrypt.", ("Local CA", "CA", "CRL")),
    ("certbot", "ACME client of the EFF used by jarvis-cert to obtain and renew Let's Encrypt certificates.",
     ("certbot",)),
    ("CH340", "WCH USB-to-serial converter chip fitted to the LCUS relay boards.", ("CH340",)),
    ("Cooldown", "Minimum delay enforced between two pulses of the garage relay (5 s by default).",
     ("cooldown*",)),
    ("CSP", "Content Security Policy: HTTP header that restricts the sources of scripts, images and frames of a page.",
     ("CSP", "Content-Security-Policy")),
    ("CSRF", "Cross-Site Request Forgery: attack that makes a logged-in user perform an action from a third-party "
     "site. Countered here by the mandatory X-Jarvis header and the SameSite=Strict cookie.", ("CSRF",)),
    ("CTS / DTR", "Control lines of a serial link: CTS (Clear To Send) is an input, DTR (Data Terminal Ready) an "
     "output. CTS is used as the logic input of the door sensor.", ("CTS", "DTR")),
    ("CycloneDX", "OWASP SBOM standard (JSON/XML format). Version used: 1.6.", ("CycloneDX",)),
    ("Deduplication", "Removal of redundant records: an unknown face very close to an unknown face seen within the "
     "hour is not recorded again.", ("deduplicat*",)),
    ("Dead zone", "Tolerance around the image center inside which the PTZ does not move (12 %).",
     ("dead zone", "deadzone")),
    ("DHCP", "Automatic IP address assignment protocol; a \"DHCP reservation\" always gives the same address to a "
     "network card.", ("DHCP",)),
    ("DNS-01", "ACME challenge proven by publishing a TXT record in the public DNS zone through the DNS provider API. "
     "It needs no inbound port open on the Internet.", ("DNS-01",)),
    ("Dry contact (potential-free)", "Electrical contact that supplies no voltage: it only closes or opens a circuit "
     "powered by the connected equipment. The only type allowed on terminal F of the Novomatic 200.",
     ("dry contact", "potential-free")),
    ("ECAPA-TDNN", "Speaker verification neural network (SpeechBrain) that produces a 192-dimension voiceprint.",
     ("ECAPA*",)),
    ("Embedding", "Representation of a face or a voice as a numeric vector; two close embeddings probably belong to "
     "the same person.", ("embedding*",)),
    ("fail2ban", "Log-driven intrusion prevention: bans the source addresses that repeatedly fail SSH or web logins.",
     ("fail2ban",)),
    ("FFmpeg", "Audio/video decoding and encoding library and tool, used for the RTSP stream and for converting the "
     "voice recordings.", ("FFmpeg",)),
    ("F/UTP", "Twisted-pair cable with an overall foil screen (Foiled / Unshielded Twisted Pairs).",
     ("F/UTP", "FTP")),
    ("FT232R / FT232RL", "FTDI USB-to-serial converter chip with TTL levels; its CTS# pin reads the door sensor. Its "
     "internal pull-up (about 200 kΩ) is weak.", ("FT232*",)),
    ("Gallery", "Set of reference embeddings of the enrolled persons, loaded in memory.", ("galler*",)),
    ("GDPR", "General Data Protection Regulation (EU 2016/679). Faces and voices are biometric data (Art. 9).",
     ("GDPR",)),
    ("Grafana", "Dashboard server of the optional monitoring stack (Loki and Prometheus data sources).",
     ("Grafana",)),
    ("H.264 / H.265", "Video compression standards (AVC / HEVC). The analyzed stream must be H.264, the only one the "
     "Intel HD 4600 decodes in hardware.", ("H.264", "H.265", "HEVC")),
    ("Half-duplex", "Operating mode in which the microphone is ignored while the loudspeaker is talking.",
     ("half-duplex",)),
    ("Hi-PoE / High PoE", "Proprietary high-power PoE (Hikvision, Dahua; \"High PoE\" at Axis), close to 802.3bt.",
     ("Hi-PoE", "High PoE")),
    ("Home Assistant", "Open-source home automation controller; consumes the Jarvis MQTT discovery messages and "
     "webhooks.", ("Home Assistant",)),
    ("HSTS", "HTTP Strict Transport Security: forces the browser to use HTTPS for the site.", ("HSTS",)),
    ("Identification by vote", "Several matching observations (3) are required before a person is declared "
     "recognized.", ("vote*", "voting")),
    ("Injector (PoE midspan)", "Unit that adds power to an Ethernet cable between a network device and a powered "
     "device.", ("injector*", "midspan")),
    ("InsightFace", "Open-source face analysis library (SCRFD detection, ArcFace recognition).", ("InsightFace",)),
    ("IoU", "Intersection over Union: ratio between the common area and the union area of two boxes; measures their "
     "overlap.", ("IoU",)),
    ("IP55 / IP65 / IP66 / IP67", "Ingress protection ratings (IEC 60529): 6 = dust-tight; 5 = water jets, "
     "6 = powerful jets, 7 = temporary immersion.", ("IP55", "IP65", "IP66", "IP67")),
    ("IR", "Infrared: invisible lighting that gives a black-and-white image at night.", ("IR", "infrared")),
    ("journald", "systemd logging service (journalctl command).", ("journald", "journalctl")),
    ("Kaldi", "Speech recognition toolkit on which Vosk is built.", ("Kaldi",)),
    ("Laplacian (variance of the)", "Image sharpness measure: a low variance means a blurred image.",
     ("Laplacian",)),
    ("LCUS-4", "4-relay board driven over USB through a CH340, serial frame A0 channel state checksum.",
     ("LCUS*",)),
    ("Let's Encrypt", "Free public certificate authority, reached with the ACME protocol.", ("Let's Encrypt",)),
    ("Lever terminal block", "Screwless connector with a lever (Wago 221 type) for 0.14 to 4 mm² conductors.",
     ("Wago", "terminal block*")),
    ("Loki", "Grafana log store; receives the Jarvis logs pushed by Alloy or Promtail (port 3100).", ("Loki",)),
    ("mDNS", "Multicast DNS (Avahi): resolves <hostname>.local on the LAN without a DNS server.", ("mDNS", "Avahi")),
    ("MJPEG", "Video stream made of a sequence of JPEG images, played directly by a browser.", ("MJPEG",)),
    ("MQTT", "Lightweight publish/subscribe messaging protocol (TCP 1883, 8883 with TLS) used by the optional "
     "smart-home bridge.", ("MQTT",)),
    ("NAT", "Network Address Translation performed by the Internet router, which isolates the LAN from the "
     "Internet.", ("NAT",)),
    ("nftables", "Linux kernel firewall (successor of iptables).", ("nftables", "nft")),
    ("node_exporter", "Prometheus agent that exposes the host metrics (CPU, memory, disk, network) on port 9100.",
     ("node_exporter",)),
    ("NO / NC / COM", "Relay terminals: COM common, NO normally open (closed when the relay is energized), NC "
     "normally closed.", ("NO", "COM")),
    ("NTP", "Network Time Protocol (UDP 123); keeps the clocks synchronized (certificates, audit timestamps).",
     ("NTP", "timesyncd", "chrony")),
    ("ONNX / ONNX Runtime", "Open AI model format and optimized inference engine (Microsoft).", ("ONNX",)),
    ("ONVIF", "IP camera interoperability standard (discovery, streaming, PTZ). Profile S: video and PTZ.",
     ("ONVIF",)),
    ("OpenVINO", "Intel inference engine optimized for its processors.", ("OpenVINO",)),
    ("openWakeWord", "Open-source wake-word detector (ONNX models).", ("openWakeWord",)),
    ("Piper", "Offline neural text-to-speech engine (VITS exported to ONNX).", ("Piper",)),
    ("PoE", "Power over Ethernet: electrical power carried by the network cable.", ("PoE",)),
    ("Porcupine", "Commercial wake-word detector from Picovoice (free key for personal use).", ("Porcupine",)),
    ("Preset", "PTZ position stored in the camera (pan, tilt, zoom).", ("preset*",)),
    ("Prometheus", "Metrics collection server; scrapes the Jarvis /metrics endpoint and node_exporter.",
     ("Prometheus",)),
    ("Proportional controller", "Control loop whose command is proportional to the measured error (here, the offset "
     "between the person and the image center).", ("proportional controller", "P controller")),
    ("PTZ", "Pan-Tilt-Zoom: camera motorized in pan, tilt and zoom.", ("PTZ",)),
    ("Pull-up", "Resistor that pulls a logic input to the high state when nothing drives it low.", ("pull-up*",)),
    ("Pulse", "Short closure (500 ms) of the relay contact, equivalent to pressing the push button.",
     ("pulse*",)),
    ("purl", "Package URL: normalized identifier of a software package (e.g. pkg:pypi/numpy@1.26.4).", ("purl",)),
    ("Reed switch", "Switch with flexible blades operated by a magnet; used as a door opening sensor.",
     ("reed",)),
    ("Restricted grammar", "Closed list of phrases imposed on the Vosk decoder: everything else is rejected as [unk], "
     "which makes recognition very robust to noise.", ("grammar",)),
    ("Retroactive identification", "Assignment, after the fact, of an unknown visitor's sightings to a person when "
     "their cluster is labeled.", ("retroactive*",)),
    ("RTSP", "Real Time Streaming Protocol (RFC 2326/7826): streaming protocol of the camera video.", ("RTSP",)),
    ("SBOM", "Software Bill of Materials: exhaustive inventory of the software components, versions and licenses.",
     ("SBOM",)),
    ("SCRFD", "Fast InsightFace face detector (Sample and Computation Redistribution).", ("SCRFD*",)),
    ("Sighting", "Time-stamped record of an identification: date, time, identity or unknown, score, photo.",
     ("sighting*",)),
    ("Similarity (cosine)", "Cosine of the angle between two vectors, from -1 to 1; equal to the dot product of "
     "normalized vectors.", ("cosine",)),
    ("Similarity threshold", "Minimum cosine between two embeddings to conclude that they belong to the same person "
     "(0.45 for faces).", ("threshold*",)),
    ("SQLite (WAL)", "Database embedded in a single file; Write-Ahead Log mode allows concurrent reads and a write.",
     ("SQLite", "WAL")),
    ("syslog", "Standard log transport (RFC 3164, UDP or TCP 514) to an rsyslog or syslog-ng server.",
     ("syslog", "rsyslog", "syslog-ng")),
    ("systemd", "Linux service manager (jarvis-core, jarvis-api units, timers).", ("systemd", "systemctl")),
    ("tmpfs", "File system held in RAM (/run).", ("tmpfs",)),
    ("TLS / HTTPS", "Encryption of web traffic (Transport Layer Security).", ("TLS", "HTTPS")),
    ("Track", "Sequence of detections of the same person over time, identified by a number (ByteTrack).",
     ("track", "tracks")),
    ("TTS", "Text-To-Speech, speech synthesis.", ("TTS", "speech synthesis")),
    ("udev", "Linux device manager; its rules give stable names (/dev/jarvis-relay).", ("udev",)),
    ("Unix socket", "Local inter-process communication channel represented by a file (/run/jarvis/core.sock).",
     ("socket*",)),
    ("Unknown cluster", "Automatic grouping of the visits of the same unknown visitor.", ("cluster*",)),
    ("uv", "Python package and environment manager (Astral).", ("=uv",)),
    ("VAAPI", "Video Acceleration API: hardware video decoding on Intel GPUs.", ("VAAPI",)),
    ("VITS", "End-to-end neural speech synthesis architecture used by Piper.", ("VITS",)),
    ("VLAN", "Virtual LAN (802.1Q) that logically separates devices on the same switch.", ("VLAN",)),
    ("Vosk", "Offline speech recognition engine built on Kaldi.", ("Vosk",)),
    ("Wake word", "Word or phrase (\"hey jarvis\") that activates command listening.", ("wake word*", "wake-word")),
    ("Watchlist", "Flagged persons whose recognition triggers an alert and never grants access.",
     ("watchlist*",)),
    ("Webhook", "HTTP request sent automatically to another service when an event occurs.", ("webhook*",)),
    ("Authorization window", "Delay (60 s) during which an authorized person recognized by the camera can command "
     "the garage by voice.", ("authorization window",)),
    ("WireGuard", "Modern, lightweight VPN protocol, recommended for remote access.", ("WireGuard",)),
    ("YOLO", "You Only Look Once: family of single-pass object detectors; YOLO11n is the nano variant.",
     ("YOLO*",)),
]

# Books: (language, topics, authors, title, edition/publisher/year, ISBN-13 or "" when not verified).
# ISBNs were checked against publisher pages and library/bookseller catalogs on 2026-09-30.
BOOKS: list[tuple[str, set[str], str, str, str, str]] = [
    # --- English
    ("en", {"vision"}, "Szeliski, Richard", "Computer Vision: Algorithms and Applications",
     "2nd ed. Springer, 2022", "978-3-030-34371-2"),
    ("en", {"vision"}, "Gonzalez, Rafael C.; Woods, Richard E.", "Digital Image Processing",
     "4th ed. Pearson, 2018", "978-0-13-335672-4"),
    ("en", {"vision"}, "Kaehler, Adrian; Bradski, Gary",
     "Learning OpenCV 3: Computer Vision in C++ with the OpenCV Library", "O'Reilly, 2016", "978-1-4919-3799-0"),
    ("en", {"vision", "face"}, "Li, Stan Z.; Jain, Anil K. (eds.)", "Handbook of Face Recognition",
     "2nd ed. Springer, 2011", "978-0-85729-931-4"),
    ("en", {"ml"}, "Goodfellow, Ian; Bengio, Yoshua; Courville, Aaron", "Deep Learning", "MIT Press, 2016",
     "978-0-262-03561-3"),
    ("en", {"ml"}, "Bishop, Christopher M.", "Pattern Recognition and Machine Learning", "Springer, 2006",
     "978-0-387-31073-2"),
    ("en", {"ml"}, "Huyen, Chip",
     "Designing Machine Learning Systems: An Iterative Process for Production-Ready Applications",
     "O'Reilly, 2022", "978-1-098-10796-3"),
    ("en", {"voice"}, "Jurafsky, Daniel; Martin, James H.", "Speech and Language Processing",
     "2nd ed. Pearson Prentice Hall, 2009", "978-0-13-187321-6"),
    ("en", {"voice"}, "Huang, Xuedong; Acero, Alex; Hon, Hsiao-Wuen",
     "Spoken Language Processing: A Guide to Theory, Algorithm, and System Development", "Prentice Hall, 2001",
     "978-0-13-022616-7"),
    ("en", {"voice"}, "Rabiner, Lawrence; Juang, Biing-Hwang", "Fundamentals of Speech Recognition",
     "Prentice Hall, 1993", "978-0-13-015157-5"),
    ("en", {"voice"}, "Taylor, Paul", "Text-to-Speech Synthesis", "Cambridge University Press, 2009",
     "978-0-521-89927-7"),
    ("en", {"control", "vision"}, "Åström, Karl Johan; Murray, Richard M.",
     "Feedback Systems: An Introduction for Scientists and Engineers", "Princeton University Press, 2008",
     "978-0-691-13576-2"),
    ("en", {"network"}, "Stevens, W. Richard; Fenner, Bill; Rudoff, Andrew M.",
     "UNIX Network Programming, Volume 1: The Sockets Networking API", "3rd ed. Addison-Wesley, 2003",
     "978-0-13-141155-5"),
    ("en", {"network", "security"}, "Ristić, Ivan",
     "Bulletproof TLS and PKI: Understanding and Deploying SSL/TLS and PKI to Secure Servers and Web Applications",
     "2nd ed. Feisty Duck, 2022", "978-1-907117-09-1"),
    ("en", {"network", "web"}, "DeJonghe, Derek",
     "NGINX Cookbook: Advanced Recipes for High-Performance Load Balancing", "3rd ed. O'Reilly, 2024",
     "978-1-098-15843-9"),
    ("en", {"network", "iot"}, "Pulver, Tim", "Hands-On Internet of Things with MQTT", "Packt, 2019",
     "978-1-78934-178-2"),
    ("en", {"monitoring"}, "Pivotto, Julien; Brazil, Brian",
     "Prometheus: Up & Running: Infrastructure and Application Performance Monitoring", "2nd ed. O'Reilly, 2023",
     "978-1-098-13114-2"),
    ("en", {"devops"}, "Geerling, Jeff",
     "Ansible for DevOps: Server and Configuration Management for Humans", "2nd ed. Leanpub, 2020", ""),
    ("en", {"linux"}, "Kerrisk, Michael",
     "The Linux Programming Interface: A Linux and UNIX System Programming Handbook", "No Starch Press, 2010",
     "978-1-59327-220-3"),
    ("en", {"linux"}, "Ward, Brian", "How Linux Works: What Every Superuser Should Know",
     "3rd ed. No Starch Press, 2021", "978-1-7185-0040-2"),
    ("en", {"python"}, "Ramalho, Luciano", "Fluent Python: Clear, Concise, and Effective Programming",
     "2nd ed. O'Reilly, 2022", "978-1-4920-5635-5"),
    ("en", {"security"}, "Anderson, Ross",
     "Security Engineering: A Guide to Building Dependable Distributed Systems", "3rd ed. Wiley, 2020",
     "978-1-119-64278-7"),
    ("en", {"electronics"}, "Horowitz, Paul; Hill, Winfield", "The Art of Electronics",
     "3rd ed. Cambridge University Press, 2015", "978-0-521-80926-9"),
    # --- French (titles as published)
    ("fr", {"ml", "vision"}, "Azencott, Chloé-Agathe", "Introduction au Machine Learning", "2nd ed. Dunod, 2022",
     "978-2-10-083476-1"),
    ("fr", {"ml", "vision"}, "Le Cun, Yann",
     "Quand la machine apprend : la révolution des neurones artificiels et de l'apprentissage profond",
     "Odile Jacob, 2019", "978-2-7381-4931-2"),
    ("fr", {"ml", "vision"}, "Géron, Aurélien",
     "Deep Learning avec Keras et TensorFlow : mise en œuvre et cas concrets", "3rd ed. Dunod, 2024",
     "978-2-10-084769-3"),
    ("fr", {"voice"}, "Haton, Jean-Paul; Cerisara, Christophe; Fohr, Dominique; Laprie, Yves; Smaïli, Kamel",
     "Reconnaissance automatique de la parole : du signal à son interprétation", "Dunod, 2006",
     "978-2-10-005842-6"),
    ("fr", {"network"}, "Tanenbaum, Andrew S.; Wetherall, David J.", "Réseaux", "5th ed. Pearson, 2011",
     "978-2-7440-7521-6"),
    ("fr", {"network"}, "Pujolle, Guy", "Les Réseaux", "2024-2026 edition (10th ed.). Eyrolles, 2024",
     "978-2-416-01433-8"),
    ("fr", {"linux"}, "Blaess, Christophe",
     "Développement système sous Linux : ordonnancement multitâche, gestion mémoire, communications, "
     "programmation réseau", "5th ed. Eyrolles, 2019", "978-2-212-67760-7"),
    ("fr", {"linux", "devops"}, "Rohaut, Sébastien; Banquet, Philippe",
     "Linux : maîtrisez l'administration du système", "7th ed. Éditions ENI, 2024", "978-2-409-04366-6"),
    ("fr", {"python"}, "Swinnen, Gérard", "Apprendre à programmer avec Python 3", "3rd ed. Eyrolles, 2012",
     "978-2-212-13434-6"),
    ("fr", {"security", "privacy"}, "Ghernaouti, Solange",
     "Cybersécurité : analyser les risques, mettre en œuvre les solutions", "8th ed. Dunod, 2025",
     "978-2-10-087183-4"),
]

# Standards, specifications and papers: (topics, HTML reference)
PAPERS: list[tuple[set[str], str]] = [
    ({"face"}, "Deng, J.; Guo, J.; Xue, N.; Zafeiriou, S. \"ArcFace: Additive Angular Margin Loss for Deep Face "
               "Recognition\". <i>CVPR</i>, 2019."),
    ({"face"}, "Guo, J.; Deng, J.; Lattas, A.; Zafeiriou, S. \"Sample and Computation Redistribution for Efficient "
               "Face Detection\" (SCRFD). <i>ICLR</i>, 2022."),
    ({"vision"}, "Zhang, Y. et al. \"ByteTrack: Multi-Object Tracking by Associating Every Detection Box\". "
                 "<i>ECCV</i>, 2022."),
    ({"vision"}, "Redmon, J.; Divvala, S.; Girshick, R.; Farhadi, A. \"You Only Look Once: Unified, Real-Time Object "
                 "Detection\". <i>CVPR</i>, 2016."),
    ({"voice"}, "Desplanques, B.; Thienpondt, J.; Demuynck, K. \"ECAPA-TDNN: Emphasized Channel Attention, Propagation "
                "and Aggregation in TDNN Based Speaker Verification\". <i>Interspeech</i>, 2020."),
    ({"voice"}, "Kim, J.; Kong, J.; Son, J. \"Conditional Variational Autoencoder with Adversarial Learning for "
                "End-to-End Text-to-Speech\" (VITS). <i>ICML</i>, 2021."),
    ({"voice"}, "Povey, D. et al. \"The Kaldi Speech Recognition Toolkit\". <i>IEEE ASRU</i>, 2011."),
    ({"network", "vision"}, "ONVIF. <i>Profile S Specification</i>; <i>PTZ Service Specification</i>. onvif.org."),
    ({"network"}, "IETF RFC 7826. <i>Real-Time Streaming Protocol Version 2.0</i>, 2016 (RTSP 1.0: RFC 2326, 1998)."),
    ({"network", "security"}, "IETF RFC 8446. <i>The Transport Layer Security (TLS) Protocol Version 1.3</i>, 2018."),
    ({"network", "security"}, "IETF RFC 8555. <i>Automatic Certificate Management Environment (ACME)</i>, 2019."),
    ({"network", "monitoring"}, "IETF RFC 3164. <i>The BSD syslog Protocol</i>, 2001."),
    ({"network", "monitoring"}, "IETF RFC 5905. <i>Network Time Protocol Version 4</i>, 2010."),
    ({"network", "monitoring"}, "OASIS. <i>MQTT Version 5.0</i>, OASIS Standard, 2019."),
    ({"network", "electronics"}, "IEEE Std 802.3bt-2018. <i>Physical Layer and Management Parameters for Power over "
                                 "Ethernet over 4 pairs</i>."),
    ({"security"}, "IETF RFC 9106. <i>Argon2 Memory-Hard Function for Password Hashing and Proof-of-Work "
                   "Applications</i>, 2021."),
    ({"security", "sbom"}, "OWASP. <i>CycloneDX Bill of Materials Specification 1.6</i>, 2024."),
    ({"privacy"}, "Regulation (EU) 2016/679 (GDPR), in particular Art. 2 (household exemption) and Art. 9 "
                  "(biometric data)."),
    ({"electronics"}, "IEC 60529. <i>Degrees of protection provided by enclosures (IP Code)</i>."),
]

# Additional index terms (beyond the glossary): (entry, search patterns)
INDEX_EXTRA: list[tuple[str, tuple[str, ...]]] = [
    ("Access rules", ("access rule*",)),
    ("Backup", ("backup*",)),
    ("Door sensor", ("door sensor*",)),
    ("Event log", ("event log", "events table")),
    ("Face search", ("search by face", "face search")),
    ("Fixed camera", ("fixed camera",)),
    ("jarvis-api", ("jarvis-api",)),
    ("jarvis-cert", ("jarvis-cert",)),
    ("jarvis-core", ("jarvis-core",)),
    ("Lenovo ThinkCentre M73 Tiny", ("M73", "Tiny")),
    ("LED indicators", ("LED*", "indicator*")),
    ("nginx", ("nginx",)),
    ("Novoferm Novomatic 200", ("Novomatic",)),
    ("Reolink Argus PT", ("Argus PT", "Reolink")),
    ("Relay", ("relay*",)),
    ("ReSpeaker", ("ReSpeaker",)),
    ("Retention", ("retention",)),
    ("Schedule (time slot)", ("schedule*", "time slot*")),
    ("Session / login", ("session*", "login")),
    ("Speaker verification", ("speaker verification", "speaker identification")),
    ("Terminal F (external pulse input)", ("terminal F",)),
    ("Ubuntu Server", ("Ubuntu",)),
    ("USB 3.0 to Gigabit adapter", ("RTL8153", "AX88179", "eth1", "USB-GbE")),
]
