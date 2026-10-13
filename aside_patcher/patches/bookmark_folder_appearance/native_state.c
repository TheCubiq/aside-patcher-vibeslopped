/* Local, per-profile folder decorations. No bookmark data is written. */
#include "native_state.h"

static const char preference_name[] = "aside.bookmark_folder_appearance";
static const char format_header[] = "v1\n";

static int equal(const char *left, const char *right, size_t size) {
    for (size_t i = 0; i < size; ++i) if (left[i] != right[i]) return 0;
    return 1;
}

static void copy(char *out, const char *source, size_t size) {
    for (size_t i = 0; i < size; ++i) out[i] = source[i];
}

BfaStringView BfaStringBytes(const BfaString *value) {
    BfaStringView result;
    if (value->bytes[23] & 0x80) {
        result.data = *(const char *const *)value->bytes;
        result.size = *(const size_t *)(value->bytes + 8);
    } else {
        result.data = (const char *)value->bytes;
        result.size = value->bytes[23];
    }
    return result;
}

int BfaValidGuid(const char *guid, size_t size) {
    if (size != 36) return 0;
    for (size_t i = 0; i < size; ++i) {
        char c = guid[i];
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (c != '-') return 0;
        } else if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return 0;
    }
    return 1;
}

int BfaFindIcon(const char *key, size_t size) {
    for (int i = 0; i < 48; ++i) {
        if (BfaNativeIcons[i].key_size == size && equal(BfaNativeIcons[i].key, key, size)) return i;
    }
    return -1;
}

int BfaValidColor(int color) {
    for (int i = 0; i < 8; ++i) if (BfaNativeColors[i] == color) return 1;
    return 0;
}

static int parse_entry(BfaStringView line, BfaAppearance *appearance) {
    if (line.size < 38 || line.data[36] != '\t' || !BfaValidGuid(line.data, 36)) return 0;
    appearance->color = -1;
    appearance->icon = -1;
    size_t separator = 37;
    while (separator < line.size && line.data[separator] != '\t') ++separator;
    if (separator == line.size) return 0;
    if (separator == 38) {
        int color = line.data[37] - '0';
        if (BfaValidColor(color)) appearance->color = color;
    }
    appearance->icon = BfaFindIcon(line.data + separator + 1, line.size - separator - 1);
    return 1;
}

static BfaStringView read_preference(void *prefs) {
    BfaStringView key = {preference_name, sizeof(preference_name) - 1};
    BfaStringView value = BfaStringBytes(BfaGetString(prefs, &key));
    if (value.size < 3 || !equal(value.data, format_header, 3)) value.size = 0;
    return value;
}

static BfaStringView next_entry(BfaStringView value, size_t *cursor) {
    size_t start = *cursor;
    while (*cursor < value.size && value.data[*cursor] != '\n') ++*cursor;
    BfaStringView line = {value.data + start, *cursor - start};
    if (*cursor < value.size) ++*cursor;
    return line;
}

void BfaReadAppearance(void *prefs, const char guid[36], BfaAppearance *result) {
    result->color = -1;
    result->icon = -1;
    if (!BfaValidGuid(guid, 36)) return;
    BfaStringView value = read_preference(prefs);
    size_t cursor = 3;
    while (cursor < value.size) {
        BfaStringView line = next_entry(value, &cursor);
        BfaAppearance appearance;
        if (parse_entry(line, &appearance) && equal(line.data, guid, 36)) *result = appearance;
    }
}

static size_t append_entry(char *out, const char *guid, BfaAppearance appearance) {
    size_t size = 36;
    copy(out, guid, size);
    out[size++] = '\t';
    if (BfaValidColor(appearance.color)) out[size++] = (char)('0' + appearance.color);
    out[size++] = '\t';
    if (appearance.icon >= 0 && appearance.icon < 48) {
        const BfaIcon *icon = &BfaNativeIcons[appearance.icon];
        copy(out + size, icon->key, icon->key_size);
        size += icon->key_size;
    }
    out[size++] = '\n';
    return size;
}

void BfaWriteAppearance(void *prefs, const char guid[36], BfaAppearance appearance) {
    if (!BfaValidGuid(guid, 36)) return;
    BfaStringView value = read_preference(prefs);
    size_t extra = 64;
    if (appearance.icon >= 0 && appearance.icon < 48) extra += BfaNativeIcons[appearance.icon].key_size;
    if (value.size > SIZE_MAX - extra) return;
    size_t capacity = value.size + extra;
    char *buffer = BfaAllocate(capacity);
    copy(buffer, format_header, 3);
    size_t written = 3, cursor = 3;
    while (cursor < value.size) {
        BfaStringView line = next_entry(value, &cursor);
        BfaAppearance existing;
        if (parse_entry(line, &existing) && !equal(line.data, guid, 36) && (existing.color >= 0 || existing.icon >= 0)) {
            written += append_entry(buffer + written, line.data, existing);
        }
    }
    if (!BfaValidColor(appearance.color)) appearance.color = -1;
    if (appearance.icon < 0 || appearance.icon >= 48) appearance.icon = -1;
    if (appearance.color >= 0 || appearance.icon >= 0) written += append_entry(buffer + written, guid, appearance);
    BfaValue owned = {{0}};
    BfaConstructString((BfaString *)owned.bytes, buffer, written);
    owned.bytes[24] = 4;
    BfaStringView key = {preference_name, sizeof(preference_name) - 1};
    BfaSetValue(prefs, &key, &owned);
    BfaFree(buffer, capacity);
}

void BfaRemoveAppearances(void *prefs, const char *guids, size_t count) {
    if (!count || count > SIZE_MAX / 36) return;
    BfaStringView value = read_preference(prefs);
    if (!value.size || value.size == SIZE_MAX) return;
    size_t capacity = value.size + 1;
    char *buffer = BfaAllocate(capacity);
    copy(buffer, format_header, 3);
    size_t written = 3, cursor = 3;
    int changed = 0;
    while (cursor < value.size) {
        BfaStringView line = next_entry(value, &cursor);
        BfaAppearance appearance;
        if (!parse_entry(line, &appearance)) continue;
        int remove = 0;
        for (size_t i = 0; i < count; ++i) {
            if (equal(line.data, guids + i * 36, 36)) { remove = 1; break; }
        }
        if (remove) changed = 1;
        else if (appearance.color >= 0 || appearance.icon >= 0) {
            written += append_entry(buffer + written, line.data, appearance);
        }
    }
    if (changed) {
        BfaValue owned = {{0}};
        BfaConstructString((BfaString *)owned.bytes, buffer, written);
        owned.bytes[24] = 4;
        BfaStringView key = {preference_name, sizeof(preference_name) - 1};
        BfaSetValue(prefs, &key, &owned);
    }
    BfaFree(buffer, capacity);
}
