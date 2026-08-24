import unittest

from display_layout import (
    DENSITY_COMPACT,
    DENSITY_VERY_COMPACT,
    PROFILE_COMPACT,
    PROFILE_VERY_COMPACT,
    PROFILE_STANDARD,
    PROFILE_WIDE,
    profile_ratios,
    recommend_layout_profile,
    recommended_density,
)


class DisplayLayoutProfileTests(unittest.TestCase):
    def test_recommended_profiles_use_logical_available_geometry(self):
        cases = (
            (1024, 768, 96, PROFILE_VERY_COMPACT),
            (1280, 720, 96, PROFILE_VERY_COMPACT),
            (1366, 768, 96, PROFILE_VERY_COMPACT),
            (1600, 900, 96, PROFILE_VERY_COMPACT),
            (1920, 1080, 96, PROFILE_WIDE),
            (1366, 768, 144, PROFILE_VERY_COMPACT),
        )
        for width, height, dpi, expected in cases:
            with self.subTest(width=width, height=height, dpi=dpi):
                self.assertEqual(
                    recommend_layout_profile(width, height, dpi),
                    expected,
                )

    def test_high_dpi_prefers_compact_density(self):
        self.assertEqual(
            recommended_density(PROFILE_STANDARD, 144),
            DENSITY_COMPACT,
        )
        self.assertEqual(
            recommended_density(PROFILE_VERY_COMPACT, 96),
            DENSITY_VERY_COMPACT,
        )

    def test_billing_ratios_keep_receipt_as_largest_panel(self):
        for profile in (
            PROFILE_VERY_COMPACT,
            PROFILE_COMPACT,
            PROFILE_STANDARD,
            PROFILE_WIDE,
        ):
            patient, catalog, receipt = profile_ratios(profile)
            self.assertAlmostEqual(patient + catalog + receipt, 1.0)
            self.assertGreater(receipt, catalog)
            self.assertGreater(catalog, patient)


if __name__ == "__main__":
    unittest.main()
