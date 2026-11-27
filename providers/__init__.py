class ProviderError(Exception):
    """A platform API failure with a user-facing message.

    transient: worth retrying automatically (network / 5xx / rate limit)
    reauth:    the account must be reconnected
    """

    def __init__(self, message, transient=False, reauth=False):
        super().__init__(message)
        self.message = message
        self.transient = transient
        self.reauth = reauth
