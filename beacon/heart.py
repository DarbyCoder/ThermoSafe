"""Personal heart-rate model.

Learns what's normal for one person, adjusts for what they're doing, and only
raises an alert when a change is sustained.

  expected = their resting normal + beta * activity_load
  z        = (heart_rate - expected) / spread
  CUSUM    = running sum of z beyond a slack; alert when it crosses a threshold
"""
import math
import statistics
from collections import deque

REST_ACT = 0.25  # activity below this counts as sitting still


class HeartModel:
    def __init__(self, prior_mu=70.0, prior_sigma=6.0, beta0=30.0,
                 rest_max=130, rest_min=40):
        self.prior_mu = prior_mu
        self.prior_sigma = prior_sigma
        self.beta0 = beta0
        self.rest_max = rest_max
        self.rest_min = rest_min

        self.k_minutes = 1.0     # the population prior is worth 1 minute of their own data
        self.learn_n = 300       # 5 minutes sitting still before alerts switch on
        self.lam = 60.0          # beta prior is worth ~1 minute of walking
        self.tau_up = 20.0       # heart speeds up fast...
        self.tau_down = 60.0     # ...and slows down slowly
        self.cusum_k = 1.0       # slack: ignore anything under 1 spread
        self.watch_h = 20.0
        self.alert_h = 45.0

        self.rest = deque(maxlen=3600)   # up to an hour of resting readings
        self.sxx = 0.0
        self.sxy = 0.0
        self.load = 0.0                  # activity load, lagged like a real heart
        self.act_short = 0.0             # for labels like "Walking"
        self.s_up = 0.0
        self.s_dn = 0.0
        self.guard_secs = 0.0
        self.bad_secs = 0.0
        self.alerting = False
        self.direction = None
        self.last_t = None
        self.hr = None
        self.expected = None
        self.z = 0.0
        self.history = deque(maxlen=600)  # (t, hr, expected, lo, hi)
        self._mu = prior_mu
        self._sigma = prior_sigma
        self._since_fit = 999

    # --- learned normal -------------------------------------------------
    def baseline(self):
        if self._since_fit >= 10:
            self._since_fit = 0
            if len(self.rest) >= 30:
                data = list(self.rest)
                med = statistics.median(data)
                mad = statistics.median(abs(x - med) for x in data)
                n = len(data) / 60.0
                k = self.k_minutes
                obs_sigma = max(2.5, 1.4826 * mad)
                self._mu = (n * med + k * self.prior_mu) / (n + k)
                self._sigma = (n * obs_sigma + k * self.prior_sigma) / (n + k)
            else:
                self._mu, self._sigma = self.prior_mu, self.prior_sigma
        return self._mu, self._sigma

    def beta(self):
        return (self.sxy + self.lam * self.beta0) / (self.sxx + self.lam)

    @property
    def learning(self):
        return len(self.rest) < self.learn_n

    # --- one reading ----------------------------------------------------
    def update(self, t, hr, quality, act):
        dt = 1.0 if self.last_t is None else max(0.0, min(5.0, t - self.last_t))
        self.last_t = t
        act = max(0.0, min(3.0, float(act or 0.0)))

        tau = self.tau_up if act > self.load else self.tau_down
        self.load += (1 - math.exp(-dt / tau)) * (act - self.load)
        self.act_short += (1 - math.exp(-dt / 5.0)) * (act - self.act_short)

        valid = hr is not None and (quality is None or quality >= 0.5) and 25 <= hr <= 230
        if not valid:
            self.bad_secs += dt
            return
        self.bad_secs = 0.0
        self.hr = float(hr)
        self._since_fit += 1

        mu, sigma = self.baseline()
        extra = max(0.0, self.load - 0.1)          # movement beyond sitting still
        spread = sigma * (1 + 0.5 * extra)        # stricter at rest, looser while moving
        expected = mu + self.beta() * extra
        z = (self.hr - expected) / spread
        self.expected, self.z = expected, z

        if self.learning:
            # until we know their normal, only the hard limits can alert
            self.s_up = self.s_dn = 0.0
        else:
            cap = 2 * self.alert_h   # so it recovers quickly once things settle
            self.s_up = min(cap, max(0.0, self.s_up + (z - self.cusum_k) * dt))
            if self.load < 0.15:
                self.s_dn = min(cap, max(0.0, self.s_dn + (-z - self.cusum_k) * dt))
            else:
                # a fast slowdown after walking is healthy, so only judge "too low" at rest
                self.s_dn *= 0.9

        resting = self.load < REST_ACT
        if resting and (self.hr > self.rest_max or self.hr < self.rest_min):
            self.guard_secs += dt
        else:
            self.guard_secs = 0.0

        if self.s_up >= self.alert_h or self.s_dn >= self.alert_h or self.guard_secs >= 30:
            self.alerting = True
        elif self.alerting and self.s_up < self.watch_h / 2 and self.s_dn < self.watch_h / 2 and self.guard_secs == 0:
            self.alerting = False
        if self.guard_secs > 0:
            self.direction = "high" if self.hr > self.rest_max else "low"
        else:
            self.direction = "high" if self.s_up >= self.s_dn else "low"

        # never learn from the emergency itself
        calm = not self.alerting and self.guard_secs == 0 and (self.learning or abs(z) < 2.5)
        if calm:
            if resting and act < REST_ACT:
                self.rest.append(self.hr)
            elif extra > 0.2:
                decay = 0.999 ** dt
                self.sxx = self.sxx * decay + extra ** 2 * dt
                self.sxy = self.sxy * decay + extra * (self.hr - mu) * dt

        self.history.append((t, round(self.hr, 1), round(expected, 1),
                             round(expected - 2 * spread, 1), round(expected + 2 * spread, 1)))

    # --- what to show ---------------------------------------------------
    def status(self, now):
        if self.last_t is None or now - self.last_t > 20:
            return "offline"
        if self.bad_secs >= 10 or self.hr is None:
            return "sensor"
        if self.alerting:
            return "alert"
        if self.s_up >= self.watch_h or self.s_dn >= self.watch_h:
            return "watch"
        if self.learning:
            return "learning"
        return "ok"

    def activity(self):
        a = self.act_short
        if a < REST_ACT:
            return "sitting"
        if a < 1.3:
            return "walking"
        return "stairs"
