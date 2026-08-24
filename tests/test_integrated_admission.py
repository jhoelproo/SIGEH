import tempfile
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from integrated_admission import (
    ADMISSION_EXECUTABLE_ENV,
    ADMISSION_EXECUTABLE_NAME,
    ADMISSION_SOURCE_ENV,
    AdmissionModuleController,
    admission_data_dir,
    admission_executable_candidates,
    admission_session_environment,
    admission_source_candidates,
    resolve_admission_executable,
    resolve_admission_source,
)


class IntegratedAdmissionTests(unittest.TestCase):
    def test_bundled_executable_is_preferred_and_resolved_offline(self):
        with tempfile.TemporaryDirectory() as root:
            app_dir = Path(root) / "app"
            bundle_dir = Path(root) / "_internal"
            executable = (
                bundle_dir
                / "admission_module"
                / ADMISSION_EXECUTABLE_NAME
            )
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"MZ")

            resolved = resolve_admission_executable(
                app_dir=app_dir,
                bundle_dir=bundle_dir,
                env={},
            )

            self.assertEqual(resolved, executable.resolve())

    def test_environment_override_is_first_candidate(self):
        override = Path("D:/Hospital/Admision.exe")
        candidates = admission_executable_candidates(
            app_dir="C:/Facturacion",
            bundle_dir="C:/Facturacion/_internal",
            env={ADMISSION_EXECUTABLE_ENV: str(override)},
        )
        self.assertEqual(candidates[0], override)

    def test_bundled_source_is_preferred_for_same_process_integration(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "_internal" / "admission_source" / "facturacion_tabs.py"
            source.parent.mkdir(parents=True)
            source.write_text("# validation source\n", encoding="utf-8")
            resolved = resolve_admission_source(
                app_dir=Path(root) / "app",
                bundle_dir=Path(root) / "_internal",
                env={},
            )
            self.assertEqual(resolved, source.resolve())

    def test_source_environment_override_is_first_candidate(self):
        source = Path("D:/Hospital/admission_source/facturacion_tabs.py")
        candidates = admission_source_candidates(
            app_dir="C:/Facturacion",
            bundle_dir="C:/Facturacion/_internal",
            env={ADMISSION_SOURCE_ENV: str(source)},
        )
        self.assertEqual(candidates[0], source)

    def test_controller_prefers_source_and_uses_current_process(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "admission_source" / "facturacion_tabs.py"
            source.parent.mkdir(parents=True)
            source.write_text("# validation source\n", encoding="utf-8")
            controller = AdmissionModuleController(
                app_dir=root,
                bundle_dir=root,
            )
            result = Mock(executable=source.resolve(), started=True, pid=os.getpid())
            with (
                patch.object(controller, "_launch_in_process", return_value=result) as embedded,
                patch("integrated_admission.subprocess.Popen") as popen,
            ):
                launched = controller.launch()
            self.assertEqual(launched.pid, os.getpid())
            embedded.assert_called_once_with(source.resolve())
            popen.assert_not_called()

    def test_data_directory_uses_programdata_without_network(self):
        result = admission_data_dir(
            {"PROGRAMDATA": r"C:\ProgramData"}
        )
        self.assertEqual(
            result,
            Path(r"C:\ProgramData")
            / "Hospital"
            / "GeneradorHojasEmergencia",
        )

    def test_controller_does_not_start_a_second_process(self):
        with tempfile.TemporaryDirectory() as root:
            executable = (
                Path(root)
                / "admission_module"
                / ADMISSION_EXECUTABLE_NAME
            )
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"MZ")

            process = Mock()
            process.pid = 4321
            process.poll.return_value = None
            with (
                patch(
                    "integrated_admission.subprocess.Popen",
                    return_value=process,
                ) as popen,
                patch(
                    "integrated_admission.admission_instance_running",
                    return_value=False,
                ),
            ):
                controller = AdmissionModuleController(
                    app_dir=root,
                    bundle_dir=root,
                    user_context={
                        "username": "aux.01",
                        "full_name": "Auxiliar Uno",
                        "role": "auxiliar",
                        "password_hash": "must-not-leak",
                    },
                    session_id="session-123",
                )
                first = controller.launch()
                second = controller.launch()

            self.assertTrue(first.started)
            self.assertFalse(second.started)
            self.assertEqual(second.pid, 4321)
            popen.assert_called_once()
            child_environment = popen.call_args.kwargs["env"]
            self.assertEqual(child_environment["HOSPITAL_OFFLINE"], "1")
            self.assertEqual(child_environment["HOSPITAL_USERNAME"], "aux.01")
            self.assertEqual(child_environment["HOSPITAL_FULL_NAME"], "Auxiliar Uno")
            self.assertEqual(child_environment["HOSPITAL_ROLE"], "auxiliar")
            self.assertEqual(child_environment["HOSPITAL_SESSION_ID"], "session-123")
            self.assertNotIn("password_hash", child_environment)
            self.assertTrue(
                child_environment["ADMISSION_DB_PATH"].endswith(
                    "pacientes.db"
                )
            )

    def test_unknown_role_is_reduced_to_auxiliary(self):
        environment = admission_session_environment(
            {"username": "test", "role": "superuser"},
            "session",
        )
        self.assertEqual(environment["HOSPITAL_ROLE"], "auxiliar")

    def test_controller_closes_only_its_own_running_process(self):
        controller = AdmissionModuleController()
        process = Mock()
        process.pid = 4321
        process.poll.return_value = None
        controller._process = process
        with patch("integrated_admission.subprocess.run") as taskkill:
            taskkill.return_value.returncode = 0
            self.assertTrue(controller.close())
        taskkill.assert_called_once()
        process.wait.assert_called_once()
        self.assertIsNone(controller._process)

    def test_machine_wide_guard_prevents_launch_from_another_controller(self):
        with tempfile.TemporaryDirectory() as root:
            executable = Path(root) / "admission_module" / ADMISSION_EXECUTABLE_NAME
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"MZ")
            with (
                patch("integrated_admission.admission_instance_running", return_value=True),
                patch("integrated_admission.subprocess.Popen") as popen,
            ):
                result = AdmissionModuleController(
                    app_dir=root,
                    bundle_dir=root,
                ).launch()
            self.assertFalse(result.started)
            self.assertIsNone(result.pid)
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
