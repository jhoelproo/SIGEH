import unittest

from display_layout import (
    DENSITY_COMPACT,
    DENSITY_VERY_COMPACT,
    PROFILE_AUTO,
    PROFILE_COMPACT,
    PROFILE_STANDARD,
    PROFILE_VERY_COMPACT,
    PROFILE_WIDE,
    profile_ratios,
    recommend_layout_profile,
    recommended_density,
    resolve_admission_layout_profile,
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

    def test_admission_profile_uses_real_embedded_viewport_and_safe_density(self):
        hospital = resolve_admission_layout_profile(
            1600,
            860,
            logical_dpi=96,
            profile_preference=PROFILE_VERY_COMPACT,
            density_preference=DENSITY_VERY_COMPACT,
            text_percent=100,
        )
        self.assertEqual(hospital.available_width, 1600)
        self.assertEqual(hospital.available_height, 860)
        self.assertTrue(hospital.two_columns)
        self.assertTrue(hospital.show_side_panel)
        self.assertLessEqual(hospital.side_panel_width, 320)
        self.assertGreaterEqual(hospital.input_min_height, 32)
        self.assertLessEqual(hospital.vertical_gap, 6)

        narrow = resolve_admission_layout_profile(
            1024,
            700,
            logical_dpi=144,
            profile_preference=PROFILE_WIDE,
            density_preference=DENSITY_VERY_COMPACT,
            text_percent=125,
        )
        self.assertFalse(narrow.two_columns)
        self.assertFalse(narrow.show_side_panel)
        self.assertLessEqual(narrow.text_percent, 110)
        self.assertGreaterEqual(narrow.input_min_height, 34)

    def test_admission_resolution_and_dpi_matrix_has_safe_metrics(self):
        for width, height in (
            (1366, 768),
            (1600, 860),
            (1600, 900),
            (1920, 1080),
        ):
            for dpi in (96.0, 120.0, 144.0):
                for profile in (
                    PROFILE_VERY_COMPACT,
                    PROFILE_COMPACT,
                    PROFILE_AUTO,
                ):
                    with self.subTest(
                        width=width,
                        height=height,
                        dpi=dpi,
                        profile=profile,
                    ):
                        resolved = resolve_admission_layout_profile(
                            width,
                            height,
                            logical_dpi=dpi,
                            profile_preference=profile,
                            density_preference=DENSITY_VERY_COMPACT,
                            text_percent=100,
                        )
                        self.assertGreaterEqual(resolved.input_min_height, 32)
                        self.assertGreater(resolved.button_height, 0)
                        self.assertGreater(resolved.horizontal_gap, 0)
                        self.assertEqual(
                            resolved.show_side_panel,
                            resolved.side_panel_width > 0,
                        )


if __name__ == "__main__":
    unittest.main()
