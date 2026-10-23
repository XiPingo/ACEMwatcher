import io
import smtplib
import ssl
import unittest
from email import message_from_string
from email.utils import parseaddr
from unittest.mock import Mock, call, patch, sentinel

import acem


class SendEmailTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(acem.os.environ, {
            "SMTP_USER": " sender@qq.com\n",
            "SMTP_PASS": " authorization-code\n",
            "EMAIL_FROM": " sender@qq.com\n",
        }, clear=True))
        self.enterContext(patch.object(acem, "RECIPIENT", "recipient@example.org"))
        self.enterContext(patch.object(acem.sys, "stdout", io.StringIO()))
        self.stderr = self.enterContext(patch.object(acem.sys, "stderr", io.StringIO()))
        self.enterContext(patch.object(
            acem.ssl, "create_default_context", return_value=sentinel.tls_context
        ))
        self.ssl_factory = self.enterContext(patch.object(acem.smtplib, "SMTP_SSL"))
        self.starttls_factory = self.enterContext(patch.object(acem.smtplib, "SMTP"))
        self.sleep = self.enterContext(patch.object(acem.time, "sleep"))
        self.smtp = self.ssl_factory.return_value

    def prepare_main(self):
        notices = [acem.Notice("Notice", "https://example.org/notice")]
        self.enterContext(patch.object(acem.sys, "argv", ["acem.py"]))
        self.enterContext(patch.object(acem, "fetch_notices", return_value=notices))
        self.enterContext(patch.object(acem, "load_snapshot", return_value=[]))
        self.enterContext(patch.object(acem, "is_legacy_snapshot", return_value=False))
        save = self.enterContext(patch.object(acem, "save_snapshot"))
        return notices, save

    def test_message_has_valid_sender_and_tracking_headers(self):
        acem.send_email("Test notice")

        sender, recipients, payload = self.smtp.sendmail.call_args.args
        message = message_from_string(payload)
        self.assertEqual(sender, "sender@qq.com")
        self.assertEqual(recipients, ["recipient@example.org"])
        self.assertEqual(parseaddr(message["From"])[1], sender)
        self.assertNotIn("=?", message["From"])
        self.assertTrue(message["Date"])
        self.assertTrue(message["Message-ID"])
        self.smtp.login.assert_called_once_with("sender@qq.com", "authorization-code")
        self.ssl_factory.assert_called_once_with(
            "smtp.qq.com", 465, timeout=30, context=sentinel.tls_context
        )
        self.starttls_factory.assert_not_called()
        self.smtp.close.assert_called_once()

    def test_temporary_connection_loss_reconnects(self):
        self.ssl_factory.side_effect = [
            smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
            self.smtp,
        ]

        acem.send_email("Test notice")

        self.assertEqual(self.ssl_factory.call_count, 2)
        self.sleep.assert_called_once_with(2)
        self.smtp.sendmail.assert_called_once()
        self.assertIn("SMTP connect failed", self.stderr.getvalue())

    def test_connection_lost_at_login_is_closed_before_retry(self):
        first = Mock()
        first.login.side_effect = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        self.ssl_factory.side_effect = [first, self.smtp]
        self.sleep.side_effect = lambda _: first.close.assert_called_once()

        acem.send_email("Test notice")

        first.sendmail.assert_not_called()
        self.smtp.sendmail.assert_called_once()
        self.assertIn("SMTP login failed", self.stderr.getvalue())

    def test_retries_are_bounded_and_report_the_stage(self):
        self.ssl_factory.side_effect = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

        with self.assertRaisesRegex(RuntimeError, r"SMTP connect failed.*attempt 3/3"):
            acem.send_email("Test notice")

        self.assertEqual(self.ssl_factory.call_count, 3)
        self.assertEqual(self.sleep.call_args_list, [call(2), call(4)])

    def test_greeting_timeout_can_retry_before_sending(self):
        first = Mock()
        first.ehlo_or_helo_if_needed.side_effect = TimeoutError("timed out")
        self.ssl_factory.side_effect = [first, self.smtp]

        acem.send_email("Test notice")

        first.login.assert_not_called()
        first.sendmail.assert_not_called()
        self.smtp.sendmail.assert_called_once()

    def test_bad_authorization_code_fails_without_retry(self):
        self.smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Authentication failed")

        with self.assertRaisesRegex(RuntimeError, "SMTP authorization code"):
            acem.send_email("Test notice")

        self.ssl_factory.assert_called_once()
        self.sleep.assert_not_called()
        self.smtp.sendmail.assert_not_called()
        self.smtp.close.assert_called_once()

    def test_disconnect_during_send_does_not_immediately_resend(self):
        self.smtp.sendmail.side_effect = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

        with self.assertRaisesRegex(RuntimeError, r"SMTP send failed.*Delivery status is unknown"):
            acem.send_email("Test notice")

        self.ssl_factory.assert_called_once()
        self.smtp.sendmail.assert_called_once()
        self.sleep.assert_not_called()
        self.smtp.close.assert_called_once()

    def test_rejected_message_still_fails(self):
        self.smtp.sendmail.side_effect = smtplib.SMTPDataError(550, b"Message rejected")

        with self.assertRaisesRegex(RuntimeError, "SMTP send failed"):
            acem.send_email("Test notice")

        self.smtp.sendmail.assert_called_once()
        self.sleep.assert_not_called()

    def test_accepted_message_updates_snapshot_even_when_quit_fails(self):
        notices, save = self.prepare_main()
        self.smtp.quit.side_effect = smtplib.SMTPResponseException(500, b"QUIT failed")

        self.assertEqual(acem.main(), 0)

        save.assert_called_once_with(notices)
        self.smtp.sendmail.assert_called_once()
        self.sleep.assert_not_called()
        self.smtp.close.assert_called_once()

    def test_failed_delivery_keeps_snapshot_unchanged(self):
        _, save = self.prepare_main()
        self.smtp.sendmail.side_effect = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

        self.assertEqual(acem.main(), 1)

        save.assert_not_called()

    def test_port_587_negotiates_tls_before_login(self):
        acem.os.environ.update({"SMTP_HOST": "smtp.example.org", "SMTP_PORT": "587"})
        smtp = self.starttls_factory.return_value

        acem.send_email("Test notice")

        self.ssl_factory.assert_not_called()
        self.starttls_factory.assert_called_once_with("smtp.example.org", 587, timeout=30)
        self.assertEqual(smtp.method_calls[:3], [
            call.starttls(context=sentinel.tls_context),
            call.ehlo_or_helo_if_needed(),
            call.login("sender@qq.com", "authorization-code"),
        ])
        smtp.sendmail.assert_called_once()

    def test_certificate_rejection_is_not_retried(self):
        self.ssl_factory.side_effect = ssl.SSLCertVerificationError("invalid certificate")

        with self.assertRaisesRegex(RuntimeError, "SSLCertVerificationError"):
            acem.send_email("Test notice")

        self.ssl_factory.assert_called_once()
        self.sleep.assert_not_called()
        self.smtp.login.assert_not_called()


if __name__ == "__main__":
    unittest.main()
