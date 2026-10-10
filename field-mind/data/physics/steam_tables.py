"""
=============================================================================
 IAPWS-IF97 STEAM PROPERTIES  --  the subset FieldMind's simulator needs
=============================================================================

Provenance of every equation and coefficient in this file:

    STANDARD: IAPWS-IF97, "Revised Release on the IAPWS Industrial
    Formulation 1997 for the Thermodynamic Properties of Water and Steam"
    (IAPWS R7-97(2012)).

        Region 1 (compressed / saturated liquid)  -- Eq. 7,  Table 2  (ni,Ii,Ji)
        Region 2 (saturated / superheated vapour) -- Eq. 15-17, Tables 10-11
        Region 4 (saturation line p<->T)          -- Eq. 30-31, Table 34 (ni)

    Specific gas constant  R = 0.461526 kJ/(kg K)   -- IF97 Eq. 1 / Table 1.

Why a hand-rolled subset and not a library: the agent core is stdlib-only and
data/generator must run without a scientific stack beyond numpy; this module
is pure `math`. It covers only 55-75 bar water/steam -- Regions 1, 2, 4. It
does NOT implement Region 3 (near-critical) and must not be called there.

VERIFICATION (STANDARD: IF97 published check values, Tables 5, 15, 35).
Run on import; a mistyped coefficient fails loudly here rather than silently
biasing the simulator:

    Tsat(10 MPa)          = 584.149488 K
    psat(500 K)           = 2.63889776 MPa
    Region 1 (3 MPa,300K) : v=1.00215168e-3 m3/kg, h=115.331273 kJ/kg
    Region 2 (3.5 kPa,300K): v=39.4913866 m3/kg,   h=2549.91145 kJ/kg
    Region 2 (30 MPa,700K) : v=5.42946619e-3 m3/kg -- the ONLY check that
        exercises the high-Ii residual terms (contribution ~ pi^24); keep it.

Saturated-line liquid/vapour properties are taken as Region 1 / Region 2
evaluated at (p, Tsat(p)). The resulting IF97 region-boundary inconsistency
is < 0.02 % in h -- below simulator noise (see data/physics/DERIVATIONS.md).

Public API (SI unless the name says otherwise):
    Tsat_K(p_Pa)                 saturation temperature
    psat_Pa(T_K)                 saturation pressure
    sat_liquid(p_Pa) -> dict     v,h,s,cp,rho at bubble point
    sat_vapour(p_Pa) -> dict     v,h,s,cp,rho at dew point
    hfg_J_kg(p_Pa)               latent heat
    rho_f_kg_m3(p_Pa)            saturated-liquid density
    rho_g_kg_m3(p_Pa)            saturated-vapour density
    h_liquid_J_kg(p_Pa, T_K)    compressed-liquid enthalpy (feedwater)
    h_vapour_J_kg(p_Pa, T_K)    superheated-steam enthalpy (main steam)
    drho_g_dp_kg_m3_per_Pa(p_Pa) numerical, for the shrink/swell term
    KGF_CM2G_PER_MPA             kg/cm2(g) <-> MPa factor (see below)
=============================================================================
"""
from __future__ import annotations

import math

# --- unit helpers -----------------------------------------------------------
# DERIVED: 1 kgf/cm2 = 98066.5 Pa exactly  (STANDARD: 1 kgf := 9.80665 N,
#   standard gravity, ISO 80000-4).  Hence  1 MPa / 98066.5 Pa = 10.19716212978
#   kgf/cm2 per MPa.  data/reference/fingerprint.json._pressure_conversion uses
#   this same number but is not its source.
#   NOTE for DERIVATIONS.md: the fingerprint converted the reference boiler's
#   pressure MPa->kg/cm2 without subtracting atmosphere, so its "99.6 kg/cm2(g)"
#   is ~1 unit high if the source reading was absolute. Immaterial for
#   calibration; recorded so the discrepancy is not chased later.
KGF_CM2G_PER_MPA = 1.0e6 / 98066.5
_ATM_PA = 101_325.0            # STANDARD: ISO 2533 standard atmosphere

R = 461.526                    # J/(kg K)   STANDARD: IF97 Table 1


def MPa(pa: float) -> float:
    return pa / 1.0e6


def kgfcm2g_to_Pa(x: float) -> float:
    """Gauge kg/cm2 -> absolute Pa."""
    return x / KGF_CM2G_PER_MPA * 1.0e6 + _ATM_PA


def Pa_to_kgfcm2g(pa: float) -> float:
    return (pa - _ATM_PA) / 1.0e6 * KGF_CM2G_PER_MPA


# =========================================================================
#  REGION 4  --  saturation line   (STANDARD: IF97 Eq. 30-31, Table 34)
# =========================================================================
_N4 = (
    0.11670521452767e4, -0.72421316703206e6, -0.17073846940092e2,
    0.12020824702470e5, -0.32325550322333e7,  0.14915108613530e2,
    -0.48232657361591e4, 0.40511340542057e6, -0.23855557567849,
    0.65017534844798e3,
)


def psat_Pa(T_K: float) -> float:
    """Saturation pressure [Pa] from temperature [K].  IF97 Eq. 30."""
    n = _N4
    th = T_K + n[8] / (T_K - n[9])
    A = th * th + n[0] * th + n[1]
    B = n[2] * th * th + n[3] * th + n[4]
    C = n[5] * th * th + n[6] * th + n[7]
    p_MPa = (2.0 * C / (-B + math.sqrt(B * B - 4.0 * A * C))) ** 4
    return p_MPa * 1.0e6


def Tsat_K(p_Pa: float) -> float:
    """Saturation temperature [K] from pressure [Pa].  IF97 Eq. 31."""
    n = _N4
    beta = (p_Pa / 1.0e6) ** 0.25
    E = beta * beta + n[2] * beta + n[5]
    F = n[0] * beta * beta + n[3] * beta + n[6]
    G = n[1] * beta * beta + n[4] * beta + n[7]
    D = 2.0 * G / (-F - math.sqrt(F * F - 4.0 * E * G))
    return (n[9] + D - math.sqrt((n[9] + D) ** 2 - 4.0 * (n[8] + n[9] * D))) / 2.0


# =========================================================================
#  REGION 1  --  compressed / saturated liquid  (STANDARD: IF97 Eq. 7, Table 2)
# =========================================================================
#  gamma(pi,tau) = sum ni (7.1 - pi)^Ii (tau - 1.222)^Ji
#  pi = p / 16.53 MPa ,  tau = 1386 K / T
_R1 = (
    (0, -2, 0.14632971213167), (0, -1, -0.84548187169114),
    (0, 0, -0.37563603672040e1), (0, 1, 0.33855169168385e1),
    (0, 2, -0.95791963387872), (0, 3, 0.15772038513228),
    (0, 4, -0.16616417199501e-1), (0, 5, 0.81214629983568e-3),
    (1, -9, 0.28319080123804e-3), (1, -7, -0.60706301565874e-3),
    (1, -1, -0.18990068218419e-1), (1, 0, -0.32529748770505e-1),
    (1, 1, -0.21841717175414e-1), (1, 3, -0.52838357969930e-4),
    (2, -3, -0.47184321073267e-3), (2, 0, -0.30001780793026e-3),
    (2, 1, 0.47661393906987e-4), (2, 3, -0.44141845330846e-5),
    (2, 17, -0.72694996297594e-15), (3, -4, -0.31679644845054e-4),
    (3, 0, -0.28270797985312e-5), (3, 6, -0.85205128120103e-9),
    (4, -5, -0.22425281908000e-5), (4, -2, -0.65171222895601e-6),
    (4, 10, -0.14341729937924e-12), (5, -8, -0.40516996860117e-6),
    (8, -11, -0.12734301741641e-8), (8, -6, -0.17424871230634e-9),
    (21, -29, -0.68762131295531e-18), (23, -31, 0.14478307828521e-19),
    (29, -38, 0.26335781662795e-22), (30, -39, -0.11947622640071e-22),
    (31, -40, 0.18228094581404e-23), (32, -41, -0.93537087292458e-25),
)
_R1_PSTAR = 16.53e6
_R1_TSTAR = 1386.0


def _region1(p_Pa: float, T_K: float) -> dict:
    pi = p_Pa / _R1_PSTAR
    tau = _R1_TSTAR / T_K
    a = 7.1 - pi
    b = tau - 1.222
    g = gp = gt = gtt = 0.0
    for I, J, nn in _R1:
        aI = a ** I
        aI1 = a ** (I - 1)
        bJ = b ** J
        bJ1 = b ** (J - 1)
        bJ2 = b ** (J - 2)
        g += nn * aI * bJ
        gp += -nn * I * aI1 * bJ
        gt += nn * aI * J * bJ1
        gtt += nn * aI * J * (J - 1) * bJ2
    v = R * T_K / p_Pa * pi * gp
    h = R * T_K * tau * gt
    s = R * (tau * gt - g)
    cp = -R * tau * tau * gtt
    return {"v": v, "rho": 1.0 / v, "h": h, "s": s, "cp": cp}


# =========================================================================
#  REGION 2  --  saturated / superheated vapour (STANDARD: IF97 Eq. 15-17)
# =========================================================================
#  ideal part   gamma0 = ln(pi) + sum n0j tau^J0j
#  residual     gammar = sum ni pi^Ii (tau - 0.5)^Ji
#  pi = p / 1 MPa ,  tau = 540 K / T
_R2_0 = (
    (0, -0.96927686500217e1), (1, 0.10086655968018e2),
    (-5, -0.56087911283020e-2), (-4, 0.71452738081455e-1),
    (-3, -0.40710498223928), (-2, 0.14240819171444e1),
    (-1, -0.43839511319450e1), (2, -0.28408632460772),
    (3, 0.21268463753307e-1),
)
_R2_R = (
    (1, 0, -0.17731742473213e-2), (1, 1, -0.17834862292358e-1),
    (1, 2, -0.45996013696365e-1), (1, 3, -0.57581259083432e-1),
    (1, 6, -0.50325278727930e-1), (2, 1, -0.33032641670203e-4),
    (2, 2, -0.18948987516315e-3), (2, 4, -0.39392777243355e-2),
    (2, 7, -0.43797295650573e-1), (2, 36, -0.26674547914087e-4),
    (3, 0, 0.20481737692309e-7), (3, 1, 0.43870667284435e-6),
    (3, 3, -0.32277677238570e-4), (3, 6, -0.15033924542148e-2),
    (3, 35, -0.40668253562649e-1), (4, 1, -0.78847309559367e-9),
    (4, 2, 0.12790717852285e-7), (4, 3, 0.48225372718507e-6),
    (5, 7, 0.22922076337661e-5), (6, 3, -0.16714766451061e-10),
    (6, 16, -0.21171472321355e-2), (6, 35, -0.23895741934104e2),
    (7, 0, -0.59059564324270e-17), (7, 11, -0.12621808899101e-5),
    (7, 25, -0.38946842435739e-1), (8, 8, 0.11256211360459e-10),
    (8, 36, -0.82311340897998e1), (9, 13, 0.19809712802088e-7),
    (10, 4, 0.10406965210174e-18), (10, 10, -0.10234747095929e-12),
    (10, 14, -0.10018179379511e-8), (16, 29, -0.80882908646985e-10),
    (16, 50, 0.10693031879409), (18, 57, -0.33662250574171),
    (20, 20, 0.89185845355421e-24), (20, 35, 0.30629316876232e-12),
    (20, 48, -0.42002467698208e-5), (21, 21, -0.59056029685639e-25),
    (22, 53, 0.37826947613457e-5), (23, 39, -0.12768608934681e-14),
    (24, 26, 0.73087610595061e-28), (24, 40, 0.55414715350778e-16),
    (24, 58, -0.94369707241210e-6),
)
_R2_TSTAR = 540.0


def _region2(p_Pa: float, T_K: float) -> dict:
    pi = p_Pa / 1.0e6
    tau = _R2_TSTAR / T_K
    # ideal-gas part
    g0 = math.log(pi)
    g0t = g0tt = 0.0
    for J, nn in _R2_0:
        g0 += nn * tau ** J
        g0t += nn * J * tau ** (J - 1)
        g0tt += nn * J * (J - 1) * tau ** (J - 2)
    g0p = 1.0 / pi          # d gamma0 / d pi
    # residual part
    b = tau - 0.5
    gr = grp = grt = grtt = 0.0
    for I, J, nn in _R2_R:
        piI = pi ** I
        piI1 = pi ** (I - 1)
        bJ = b ** J
        bJ1 = b ** (J - 1)
        bJ2 = b ** (J - 2)
        gr += nn * piI * bJ
        grp += nn * I * piI1 * bJ
        grt += nn * piI * J * bJ1
        grtt += nn * piI * J * (J - 1) * bJ2
    v = R * T_K / p_Pa * pi * (g0p + grp)
    h = R * T_K * tau * (g0t + grt)
    s = R * (tau * (g0t + grt) - (g0 + gr))
    cp = -R * tau * tau * (g0tt + grtt)
    return {"v": v, "rho": 1.0 / v, "h": h, "s": s, "cp": cp}


# =========================================================================
#  PUBLIC API
# =========================================================================
def sat_liquid(p_Pa: float) -> dict:
    """Saturated-liquid properties at pressure p.  Region 1 at (p, Tsat(p))."""
    return _region1(p_Pa, Tsat_K(p_Pa))


def sat_vapour(p_Pa: float) -> dict:
    """Saturated-vapour properties at pressure p.  Region 2 at (p, Tsat(p))."""
    return _region2(p_Pa, Tsat_K(p_Pa))


def hfg_J_kg(p_Pa: float) -> float:
    return sat_vapour(p_Pa)["h"] - sat_liquid(p_Pa)["h"]


def rho_f_kg_m3(p_Pa: float) -> float:
    return sat_liquid(p_Pa)["rho"]


def rho_g_kg_m3(p_Pa: float) -> float:
    return sat_vapour(p_Pa)["rho"]


def h_liquid_J_kg(p_Pa: float, T_K: float) -> float:
    """Compressed-liquid (feedwater) enthalpy. Clamped to the bubble point."""
    Ts = Tsat_K(p_Pa)
    return _region1(p_Pa, min(T_K, Ts))["h"]


def h_vapour_J_kg(p_Pa: float, T_K: float) -> float:
    """Superheated-steam (main steam) enthalpy. Clamped to the dew point."""
    Ts = Tsat_K(p_Pa)
    return _region2(p_Pa, max(T_K, Ts + 1e-6))["h"]


def drho_g_dp_kg_m3_per_Pa(p_Pa: float) -> float:
    """d(rho_g)/dp along the saturation line -- central difference.

    Used by the simulator's shrink/swell term (data/physics/DERIVATIONS.md
    section 3): a rising drum pressure compresses existing steam voids.
    """
    dp = max(1.0e3, p_Pa * 1.0e-4)
    return (rho_g_kg_m3(p_Pa + dp) - rho_g_kg_m3(p_Pa - dp)) / (2.0 * dp)


# =========================================================================
#  IMPORT-TIME SELF-TEST   (STANDARD: IF97 published verification values)
# =========================================================================
def _rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b)


def _self_test() -> None:
    tol = 1.0e-7
    checks = []

    # Region 4 -- IF97 Table 35
    checks.append(("Tsat(10 MPa)", Tsat_K(10.0e6), 584.149488, 1e-6))
    checks.append(("Tsat(0.1 MPa)", Tsat_K(0.1e6), 372.755919, 1e-6))
    checks.append(("Tsat(1 MPa)", Tsat_K(1.0e6), 453.035632, 1e-6))
    checks.append(("psat(300 K)", MPa(psat_Pa(300.0)), 0.353658941e-2, 1e-6))
    checks.append(("psat(500 K)", MPa(psat_Pa(500.0)), 2.63889776, 1e-6))
    checks.append(("psat(600 K)", MPa(psat_Pa(600.0)), 12.3443146, 1e-6))
    # round trip
    checks.append(("Tsat(psat(550 K))", Tsat_K(psat_Pa(550.0)), 550.0, 1e-8))

    # Region 1 -- IF97 Table 5
    r1 = _region1(3.0e6, 300.0)
    checks.append(("R1(3MPa,300K).v", r1["v"], 0.100215168e-2, tol))
    checks.append(("R1(3MPa,300K).h", r1["h"] / 1000.0, 0.115331273e3, tol))
    checks.append(("R1(3MPa,300K).s", r1["s"] / 1000.0, 0.392294792, tol))
    r1b = _region1(80.0e6, 300.0)
    checks.append(("R1(80MPa,300K).v", r1b["v"], 0.971180894e-3, tol))
    checks.append(("R1(80MPa,300K).h", r1b["h"] / 1000.0, 0.184142828e3, tol))
    r1c = _region1(3.0e6, 500.0)
    checks.append(("R1(3MPa,500K).v", r1c["v"], 0.120241800e-2, tol))
    checks.append(("R1(3MPa,500K).h", r1c["h"] / 1000.0, 0.975542239e3, tol))

    # Region 2 -- IF97 Table 15
    r2 = _region2(0.0035e6, 300.0)
    checks.append(("R2(3.5kPa,300K).v", r2["v"], 0.394913866e2, tol))
    checks.append(("R2(3.5kPa,300K).h", r2["h"] / 1000.0, 0.254991145e4, tol))
    checks.append(("R2(3.5kPa,300K).s", r2["s"] / 1000.0, 0.852238967e1, tol))
    r2b = _region2(0.0035e6, 700.0)
    checks.append(("R2(3.5kPa,700K).v", r2b["v"], 0.923015898e2, tol))
    checks.append(("R2(3.5kPa,700K).h", r2b["h"] / 1000.0, 0.333568375e4, tol))
    r2c = _region2(30.0e6, 700.0)
    checks.append(("R2(30MPa,700K).v", r2c["v"], 0.542946619e-2, tol))
    checks.append(("R2(30MPa,700K).h", r2c["h"] / 1000.0, 0.263149474e4, tol))

    bad = [(name, got, exp, _rel(got, exp))
           for name, got, exp, t in checks if _rel(got, exp) > t]
    if bad:
        lines = "\n".join(
            f"  {name}: got {got!r}, expected {exp!r}  (rel err {e:.2e})"
            for name, got, exp, e in bad)
        raise AssertionError(
            "IAPWS-IF97 self-test FAILED -- a coefficient is wrong:\n" + lines)


_self_test()


if __name__ == "__main__":
    print("IAPWS-IF97 self-test passed.\n")
    for pg in (55.0, 66.0, 67.0, 72.0):
        p = kgfcm2g_to_Pa(pg)
        L, V = sat_liquid(p), sat_vapour(p)
        print(f"p = {pg:5.1f} kg/cm2(g) = {MPa(p):.4f} MPa abs")
        print(f"   Tsat   = {Tsat_K(p) - 273.15:7.2f} degC")
        print(f"   rho_f  = {L['rho']:8.2f} kg/m3     rho_g = {V['rho']:7.3f} kg/m3")
        print(f"   h_f    = {L['h'] / 1000:8.2f} kJ/kg    h_g   = {V['h'] / 1000:7.2f} kJ/kg")
        print(f"   h_fg   = {hfg_J_kg(p) / 1000:8.2f} kJ/kg")
        print(f"   drho_g/dp = {drho_g_dp_kg_m3_per_Pa(p):.3e} kg/m3/Pa")
    # main steam 66 kg/cm2(g), 495 degC ; feedwater 70 bar, 150 degC
    p_ms = kgfcm2g_to_Pa(66.0)
    print(f"\nmain steam  h(66 kg/cm2g, 495 C) = "
          f"{h_vapour_J_kg(p_ms, 495 + 273.15) / 1000:.2f} kJ/kg")
    print(f"feedwater   h(70 bar, 150 C)      = "
          f"{h_liquid_J_kg(7.0e6, 150 + 273.15) / 1000:.2f} kJ/kg")
