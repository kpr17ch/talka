class VoiceBridgeError(Exception):
    pass


class ValidationError(VoiceBridgeError):
    pass


class STTError(VoiceBridgeError):
    pass


class OpenClawError(VoiceBridgeError):
    pass


class OpenClawBinaryNotFound(OpenClawError):
    pass


class OpenClawNonZeroExit(OpenClawError):
    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


class OpenClawTimeout(OpenClawError):
    pass


class OpenClawCancelled(OpenClawError):
    pass


class OpenClawInvalidJson(OpenClawError):
    pass


class OpenClawEmptyAssistant(OpenClawError):
    pass


class TTSError(VoiceBridgeError):
    pass
