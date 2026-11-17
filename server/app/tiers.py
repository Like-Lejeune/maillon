"""Classement d'une machine en niveau A, B ou C selon la mémoire de son GPU.

Les seuils sont un peu sous les tailles commerciales (24, 12, 8 Go), car les
pilotes annoncent toujours un peu moins que la capacité nominale
(par exemple 24 564 Mio pour une carte de 24 Go).
"""

TIER_THRESHOLDS_MB = (
    ("A", 22_000),  # 24 Go et plus
    ("B", 11_000),  # 12 à 16 Go
    ("C", 7_500),  # 8 Go
)

MIN_RAM_MB = 15_000  # 16 Go de mémoire vive
MIN_DISK_FREE_GB = 50


def compute_tier(vram_mb: int | None, ram_mb: int | None, disk_free_gb: int | None) -> str | None:
    """Renvoie le niveau, ou None si la machine ne remplit pas les conditions minimales."""
    if not vram_mb or (ram_mb or 0) < MIN_RAM_MB or (disk_free_gb or 0) < MIN_DISK_FREE_GB:
        return None
    for tier, threshold in TIER_THRESHOLDS_MB:
        if vram_mb >= threshold:
            return tier
    return None


def ineligibility_reason(vram_mb: int | None, ram_mb: int | None, disk_free_gb: int | None) -> str | None:
    if not vram_mb:
        return "Aucun GPU NVIDIA détecté."
    if vram_mb < TIER_THRESHOLDS_MB[-1][1]:
        return "Le GPU a moins de 8 Go de mémoire."
    if (ram_mb or 0) < MIN_RAM_MB:
        return "La machine a moins de 16 Go de mémoire vive."
    if (disk_free_gb or 0) < MIN_DISK_FREE_GB:
        return f"Moins de {MIN_DISK_FREE_GB} Go libres sur le disque."
    return None
