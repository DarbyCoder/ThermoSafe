#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_mac.h>
#include <algorithm>
#include <cstring>

namespace {
constexpr uint32_t PACKET_MAGIC = 0x48534D31; // HSM1
constexpr uint8_t PROTOCOL_VERSION = 1;
constexpr uint8_t RADIO_CHANNEL = 6;
constexpr uint8_t MAX_NODES = 32;
constexpr uint8_t MAX_HOPS = 4;
constexpr uint32_t CLAIM_INTERVAL_MS = 700;
constexpr uint32_t ELECTION_WINDOW_MS = 3500;
constexpr uint32_t BEACON_INTERVAL_MS = 1000;
constexpr uint32_t JOIN_INTERVAL_MS = 1500;
constexpr uint32_t NODE_TIMEOUT_MS = 10000;
constexpr uint32_t MASTER_TIMEOUT_MS = 6000;

enum class PacketType : uint8_t { Claim = 1, Beacon = 2, Join = 3, Heartbeat = 4 };
enum class Role : uint8_t { Electing, Master, Worker };

struct __attribute__((packed)) Packet {
    uint32_t magic;
    uint8_t version;
    uint8_t type;
    uint8_t hops;
    uint8_t reserved;
    uint32_t nodeId;
    uint32_t originId;
    uint32_t parentId;
    uint32_t masterId;
    uint32_t sequence;
    char name[20];
};

struct Incoming {
    Packet packet;
    uint8_t mac[6];
};

struct Node {
    uint32_t id = 0;
    uint32_t parentId = 0;
    uint32_t lastSeen = 0;
    uint8_t hops = 0;
    char name[20] = {};
    bool used = false;
};

struct SeenAnnouncement {
    uint32_t originId = 0;
    uint32_t sequence = 0;
};

QueueHandle_t incomingQueue = nullptr;
Preferences preferences;
Node nodes[MAX_NODES];
SeenAnnouncement seen[64];
uint8_t seenCursor = 0;
Role role = Role::Electing;
uint32_t ownId = 0;
uint32_t masterId = 0;
uint32_t bootAt = 0;
uint32_t electionEndsAt = 0;
uint32_t masterLastSeen = 0;
uint32_t lastClaim = 0;
uint32_t lastBeacon = 0;
uint32_t lastJoin = 0;
uint32_t packetSequence = 0;
uint32_t topologyRevision = 0;
char ownName[20] = {};
bool topologyDirty = true;
bool hasMasterBeacon = false;
bool electionRestarted = false;
bool customName = false;

uint32_t idFromMac(const uint8_t* mac) {
    uint32_t id = (uint32_t(mac[2]) << 24) | (uint32_t(mac[3]) << 16)
        | (uint32_t(mac[4]) << 8) | uint32_t(mac[5]);
    return id ? id : 1;
}

void printMac(const uint8_t* mac) {
    Serial.printf("%02X:%02X:%02X:%02X:%02X:%02X",
                  mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

void onReceive(const uint8_t* mac, const uint8_t* data, int length) {
    if (!incomingQueue || length != int(sizeof(Packet))) return;
    Incoming incoming{};
    memcpy(&incoming.packet, data, sizeof(Packet));
    memcpy(incoming.mac, mac, sizeof(incoming.mac));
    if (incoming.packet.magic != PACKET_MAGIC || incoming.packet.version != PROTOCOL_VERSION) return;
    xQueueSend(incomingQueue, &incoming, 0);
}

bool sendPacket(PacketType type, uint32_t origin = 0, uint32_t parent = 0,
                uint8_t hops = 0, const char* name = nullptr, uint32_t master = 0,
                uint32_t sequence = 0) {
    Packet packet{};
    packet.magic = PACKET_MAGIC;
    packet.version = PROTOCOL_VERSION;
    packet.type = static_cast<uint8_t>(type);
    packet.hops = hops;
    packet.nodeId = ownId;
    packet.originId = origin ? origin : ownId;
    packet.parentId = parent;
    packet.masterId = master;
    packet.sequence = sequence ? sequence : ++packetSequence;
    strncpy(packet.name, name ? name : ownName, sizeof(packet.name) - 1);
    const uint8_t broadcast[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    const esp_err_t result = esp_now_send(broadcast,
        reinterpret_cast<const uint8_t*>(&packet), sizeof(packet));
    return result == ESP_OK;
}

int findNode(uint32_t id) {
    for (int i = 0; i < MAX_NODES; ++i) if (nodes[i].used && nodes[i].id == id) return i;
    return -1;
}

int upsertNode(uint32_t id) {
    int index = findNode(id);
    if (index >= 0) return index;
    for (int i = 0; i < MAX_NODES; ++i) {
        if (!nodes[i].used) {
            nodes[i] = Node{};
            nodes[i].used = true;
            nodes[i].id = id;
            topologyDirty = true;
            ++topologyRevision;
            return i;
        }
    }
    return -1;
}

bool alreadyRelayed(uint32_t origin, uint32_t sequence) {
    for (const auto& entry : seen) {
        if (entry.originId == origin && entry.sequence == sequence) return true;
    }
    seen[seenCursor].originId = origin;
    seen[seenCursor].sequence = sequence;
    seenCursor = uint8_t((seenCursor + 1) % 64);
    return false;
}

void rememberNode(uint32_t id, uint32_t parent, uint8_t hops,
                  uint32_t timestamp, const char* name) {
    if (id == ownId || (role != Role::Master && masterId == 0)) return;
    const int index = upsertNode(id);
    if (index < 0) return;
    Node& node = nodes[index];
    if (!node.lastSeen || node.parentId != parent || node.hops != hops) {
        topologyDirty = true;
        ++topologyRevision;
    }
    node.parentId = parent ? parent : masterId;
    node.hops = hops;
    node.lastSeen = timestamp;
    if (name && name[0]) {
        strncpy(node.name, name, sizeof(node.name) - 1);
        node.name[sizeof(node.name) - 1] = '\0';
    }
}

void printTopology() {
    static uint32_t lastPrintedAt = 0;
    const uint32_t now = millis();
    if (!topologyDirty && now - lastPrintedAt < 5000) return;
    lastPrintedAt = now;
    topologyDirty = false;
    Serial.printf("\n[TOPOLOGY] revision=%lu role=%s master=%06lX\n",
                  static_cast<unsigned long>(topologyRevision),
                  role == Role::Master ? "MASTER" : role == Role::Worker ? "WORKER" : "ELECTING",
                  static_cast<unsigned long>(masterId));
    if (role == Role::Master) {
        Serial.printf("  \"%06lX\" [label=\"%s\\nMASTER\"];\n",
                      static_cast<unsigned long>(ownId), ownName);
        for (const auto& node : nodes) {
            if (!node.used) continue;
            Serial.printf("  \"%06lX\" [label=\"%s\\n%s\"];\n",
                          static_cast<unsigned long>(node.id),
                          node.name[0] ? node.name : "Worker",
                          millis() - node.lastSeen <= NODE_TIMEOUT_MS ? "ONLINE" : "STALE");
            Serial.printf("  \"%06lX\" -> \"%06lX\";\n",
                          static_cast<unsigned long>(node.parentId ? node.parentId : ownId),
                          static_cast<unsigned long>(node.id));
        }
    } else if (role == Role::Worker) {
        Serial.printf("  master %06lX is %s; this node %06lX (%s)\n",
                      static_cast<unsigned long>(masterId),
                      millis() - masterLastSeen <= MASTER_TIMEOUT_MS ? "reachable" : "lost",
                      static_cast<unsigned long>(ownId), ownName);
    }
}

void handlePacket(const Incoming& incoming, uint32_t now) {
    const Packet& packet = incoming.packet;
    const uint32_t senderId = idFromMac(incoming.mac);
    const PacketType type = static_cast<PacketType>(packet.type);

    if (type == PacketType::Claim) {
        if (role == Role::Electing && packet.nodeId != ownId) {
            const int index = upsertNode(packet.nodeId);
            if (index >= 0) {
                nodes[index].lastSeen = now;
                strncpy(nodes[index].name, packet.name, sizeof(nodes[index].name) - 1);
            }
        }
        return;
    }

    if (type == PacketType::Beacon) {
        if (packet.nodeId == ownId) return;
        if (role == Role::Electing || (role == Role::Worker && packet.nodeId == masterId)) {
            if (role == Role::Electing) {
                role = Role::Worker;
                masterId = packet.nodeId;
                Serial.printf("[MESH] joined master=%06lX (%s)\n",
                              static_cast<unsigned long>(masterId), packet.name);
            }
            if (packet.nodeId == masterId) {
                masterLastSeen = now;
                hasMasterBeacon = true;
            }
        }
        if (role != Role::Master && !alreadyRelayed(packet.nodeId, packet.sequence)
                && packet.hops < MAX_HOPS) {
            sendPacket(PacketType::Beacon, packet.nodeId, packet.parentId,
                       packet.hops + 1, packet.name, packet.nodeId, packet.sequence);
        }
        return;
    }

    if (type == PacketType::Join || type == PacketType::Heartbeat) {
        const uint32_t originId = packet.originId ? packet.originId : packet.nodeId;
        if (role == Role::Master && originId != ownId) {
            const uint32_t parent = packet.parentId ? packet.parentId
                : senderId == originId ? ownId : senderId;
            const int oldIndex = findNode(originId);
            const bool wasNew = oldIndex < 0;
            rememberNode(originId, parent, packet.hops, now, packet.name);
            if (wasNew) {
                Serial.printf("[MESH] worker joined id=%06lX name=\"%s\" via=%06lX hops=%u\n",
                              static_cast<unsigned long>(originId), packet.name,
                              static_cast<unsigned long>(parent), packet.hops);
            }
        }
        if (role != Role::Master && packet.masterId == masterId && originId != ownId
                && packet.hops < MAX_HOPS && !alreadyRelayed(originId, packet.sequence)) {
            const uint32_t parent = packet.parentId ? packet.parentId : ownId;
            sendPacket(PacketType::Heartbeat, originId, parent,
                       packet.hops + 1, packet.name, packet.masterId, packet.sequence);
        }
        return;
    }
}

void beginElection(uint32_t now, const char* reason) {
    role = Role::Electing;
    masterId = 0;
    hasMasterBeacon = false;
    electionRestarted = true;
    electionEndsAt = now + ELECTION_WINDOW_MS;
    lastClaim = 0;
    Serial.printf("[MESH] election started (%s)\n", reason);
}

void processSerial() {
    static char line[64];
    static size_t length = 0;
    while (Serial.available()) {
        const char c = char(Serial.read());
        if (c == '\r') continue;
        if (c != '\n' && length < sizeof(line) - 1) {
            line[length++] = c;
            continue;
        }
        line[length] = '\0';
        if (strncmp(line, "name=", 5) == 0 && line[5]) {
            strncpy(ownName, line + 5, sizeof(ownName) - 1);
            ownName[sizeof(ownName) - 1] = '\0';
            preferences.putString("name", ownName);
            Serial.printf("[NAME] saved as %s; it will appear in the next join/heartbeat.\n", ownName);
            topologyDirty = true;
        } else if (strcmp(line, "topology") == 0) {
            topologyDirty = true;
            Serial.println("[MESH] topology snapshot requested.");
        } else if (line[0]) {
            Serial.println("Type name=Worker Name to save this node's display name, or topology to refresh the graph.");
        }
        length = 0;
    }
}

void expireNodes(uint32_t now) {
    for (auto& node : nodes) {
        if (node.used && node.lastSeen && now - node.lastSeen > NODE_TIMEOUT_MS) {
            node.used = false;
            topologyDirty = true;
            ++topologyRevision;
            Serial.printf("[MESH] node offline id=%06lX\n", static_cast<unsigned long>(node.id));
        }
    }
}

void runElection(uint32_t now) {
    if (now - lastClaim >= CLAIM_INTERVAL_MS) {
        lastClaim = now;
        sendPacket(PacketType::Claim);
    }
    if (int32_t(now - electionEndsAt) < 0) return;

    uint32_t winner = ownId;
    for (const auto& node : nodes) {
        if (node.used && node.lastSeen && now - node.lastSeen < ELECTION_WINDOW_MS
                && node.id < winner) winner = node.id;
    }
    if (winner == ownId) {
        role = Role::Master;
        masterId = ownId;
        if (!customName) {
            strncpy(ownName, "Site Manager", sizeof(ownName) - 1);
            preferences.putString("name", ownName);
            customName = true;
        }
        Serial.printf("[MESH] MASTER elected id=%06lX name=\"%s\" channel=%u\n",
                      static_cast<unsigned long>(ownId), ownName, RADIO_CHANNEL);
        topologyDirty = true;
        ++topologyRevision;
    } else {
        role = Role::Worker;
        masterId = winner;
        masterLastSeen = now;
        Serial.printf("[MESH] WORKER elected; master=%06lX channel=%u\n",
                      static_cast<unsigned long>(masterId), RADIO_CHANNEL);
    }
    electionRestarted = false;
}

} // namespace

void setup() {
    Serial.begin(115200);
    delay(250);
    preferences.begin("hs-mesh", false);
    const String savedName = preferences.getString("name", "");
    customName = savedName.length() > 0;
    strncpy(ownName, savedName.c_str(), sizeof(ownName) - 1);
    ownName[sizeof(ownName) - 1] = '\0';

    uint8_t mac[6]{};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    ownId = idFromMac(mac);
    if (!ownName[0]) snprintf(ownName, sizeof(ownName), "Node-%06lX", static_cast<unsigned long>(ownId));

    Serial.println("\nHeatSentinel ESP-NOW mesh-only firmware");
    Serial.print("[NODE] id=");
    Serial.printf("%06lX mac=", static_cast<unsigned long>(ownId));
    printMac(mac);
    Serial.printf(" name=\"%s\"\n", ownName);
    Serial.printf("[RADIO] ESP-NOW broadcast, fixed channel %u; no sensors/Wi-Fi/backend used\n", RADIO_CHANNEL);
    Serial.println("[SETUP] Send name=Site Manager or name=Worker Name to label this device.");

    WiFi.mode(WIFI_STA);
    WiFi.disconnect(false, false);
    esp_wifi_set_channel(RADIO_CHANNEL, WIFI_SECOND_CHAN_NONE);
    incomingQueue = xQueueCreate(32, sizeof(Incoming));
    if (!incomingQueue || esp_now_init() != ESP_OK) {
        Serial.println("[ERROR] ESP-NOW initialization failed; check board/USB power and reset.");
        return;
    }
    esp_now_register_recv_cb(onReceive);
    esp_now_peer_info_t broadcastPeer{};
    memset(broadcastPeer.peer_addr, 0xFF, sizeof(broadcastPeer.peer_addr));
    broadcastPeer.channel = RADIO_CHANNEL;
    broadcastPeer.encrypt = false;
    if (esp_now_add_peer(&broadcastPeer) != ESP_OK) {
        Serial.println("[ERROR] Could not register broadcast peer.");
        return;
    }

    bootAt = millis();
    electionEndsAt = bootAt + ELECTION_WINDOW_MS;
    Serial.printf("[MESH] listening; automatic master election in %lu ms\n",
                  static_cast<unsigned long>(ELECTION_WINDOW_MS));
}

void loop() {
    const uint32_t now = millis();
    processSerial();
    Incoming incoming{};
    while (incomingQueue && xQueueReceive(incomingQueue, &incoming, 0) == pdTRUE) {
        handlePacket(incoming, now);
    }

    if (role == Role::Electing) {
        runElection(now);
    } else if (role == Role::Master) {
        if (now - lastBeacon >= BEACON_INTERVAL_MS) {
            lastBeacon = now;
            sendPacket(PacketType::Beacon, ownId, 0, 0, ownName, ownId);
        }
    } else {
        if (now - lastJoin >= JOIN_INTERVAL_MS) {
            lastJoin = now;
            sendPacket(PacketType::Join, ownId, 0, 0, ownName, masterId);
        }
        if (now - masterLastSeen > MASTER_TIMEOUT_MS && !electionRestarted) {
            beginElection(now, "master beacon lost");
        }
    }

    expireNodes(now);
    printTopology();

    static uint32_t lastStatus = 0;
    if (now - lastStatus >= 1000) {
        lastStatus = now;
        const char* state = role == Role::Master ? "MASTER" : role == Role::Worker ? "WORKER" : "ELECTING";
        Serial.printf("[STATUS] node=%06lX role=%s master=%06lX workers=%u channel=%u\n",
                      static_cast<unsigned long>(ownId), state,
                      static_cast<unsigned long>(masterId),
                      static_cast<unsigned>(std::count_if(std::begin(nodes), std::end(nodes),
                          [](const Node& node) { return node.used; })), RADIO_CHANNEL);
    }
    delay(1);
}
