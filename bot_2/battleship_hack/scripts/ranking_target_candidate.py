"""Offline target-inference candidate; not enabled for ranking runs."""
import time
from collections import Counter
from battleship_hack.game import FleetMC


class FleetMCTarget(FleetMC):
    """Ranking experiment: full-fleet weighted inference when finishing a hit.

    Hunt and placement remain the fleet-mc baseline. The fixed ship order gives
    every labelled configuration the same multiplicity; SIS weights correct
    sequential packing bias before conditioning on coverage of all open hits.
    """
    def choose(self) -> tuple[Cell, str]:
        if not self.hits or self.rules["touchRule"] != "NO_TOUCHING_EVEN_DIAGONALLY":
            return super().choose()
        hits = self.mask(self.hits)
        candidates = {length: [p for p in options if p[0] & hits != p[0]]
                      for length, options in self.candidates().items()}
        lengths = sorted((length for length, count in self.remaining.items() for _ in range(count)), reverse=True)
        legal = self.board - self.used - self.empty
        deadline = time.monotonic() + min(0.04, self.rules["moveTimeoutSeconds"] * 0.06)
        occupancy = Counter()
        samples, total_weight, square_weight = 0, 0, 0
        for _ in range(8000):
            if samples >= 256 or time.monotonic() >= deadline:
                break
            blocked, covered, weight, targets = 0, 0, 1, []
            for length in lengths:
                available = [p for p in candidates[length] if not p[0] & blocked]
                if not available:
                    break
                weight *= len(available)
                mask, halo, cells = self.rng.choice(available)
                blocked |= mask | halo
                covered |= mask & hits
                if mask & hits:
                    targets.extend(c for c in cells if c in legal)
            else:
                if covered != hits:
                    continue
                samples += 1
                total_weight += weight
                square_weight += weight * weight
                for cell in targets:
                    occupancy[cell] += weight
        effective = total_weight * total_weight / square_weight if square_weight else 0
        if effective < 30 or not occupancy:
            cell, reason = self.density_choice(self.candidates())
            return cell, f"target fleet-mc fallback samples={samples}; ess={effective:.1f}; {reason}"
        best = max(occupancy.values())
        cell = self.rng.choice(sorted(c for c, score in occupancy.items() if score == best))
        return cell, f"target fleet-mc weighted; samples={samples}; ess={effective:.1f}; estimated_hit={best/total_weight:.3f}"


