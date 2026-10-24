using System.Text;
using System.Text.Json;
using FinCore.Application;
using FinCore.Infrastructure;
using FluentAssertions;
using Xunit;

namespace FinCore.Tests;

public class PasswordHasherTests
{
    private readonly CustomerPasswordHasher _hasher = new();

    [Fact]
    public void Hash_and_verify_roundtrip()
    {
        var hash = _hasher.Hash("user@example.com", "correct horse battery staple");
        _hasher.Verify("user@example.com", hash, "correct horse battery staple").Should().BeTrue();
    }

    [Fact]
    public void Wrong_password_fails()
    {
        var hash = _hasher.Hash("user@example.com", "correct horse battery staple");
        _hasher.Verify("user@example.com", hash, "wrong horse battery staple").Should().BeFalse();
    }

    [Fact]
    public void Hash_format_is_pbkdf2_sha256()
    {
        var hash = _hasher.Hash("user@example.com", "whatever-password-123");
        var parts = hash.Split('$');
        parts.Should().HaveCount(4);
        parts[0].Should().Be("pbkdf2_sha256");
        int.Parse(parts[1]).Should().Be(600_000);
    }

    [Fact]
    public void Salts_are_unique_per_hash()
    {
        var first = _hasher.Hash("user@example.com", "same-password");
        var second = _hasher.Hash("user@example.com", "same-password");
        first.Should().NotBe(second);
    }

    [Fact]
    public void Malformed_hash_is_rejected()
    {
        _hasher.Verify("user@example.com", "garbage", "whatever-password-123").Should().BeFalse();
        _hasher.Verify("user@example.com", "pbkdf2_sha256$100$abc", "whatever-password-123").Should().BeFalse();
    }
}

public class PaymentRequestHashTests
{
    [Fact]
    public void Request_hash_matches_python_canonical_json()
    {
        var command = new CreatePaymentCommand(
            Guid.Parse("1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f"),
            Guid.Parse("0b9e6b6e-8f0a-4a1e-9c2d-3f4a5b6c7d8e"),
            50000, "USD", null);

        var canonical = "{\"amount_minor\":50000," +
            "\"beneficiary_id\":\"0b9e6b6e-8f0a-4a1e-9c2d-3f4a5b6c7d8e\"," +
            "\"currency\":\"USD\",\"reference\":null," +
            "\"source_account_id\":\"1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f\"}";
        var expected = Convert.ToHexStringLower(
            System.Security.Cryptography.SHA256.HashData(Encoding.UTF8.GetBytes(canonical)));

        PaymentService.CreateRequestHash(command).Should().Be(expected);
    }

    [Fact]
    public void Reference_is_included_when_present()
    {
        var withReference = new CreatePaymentCommand(
            Guid.Parse("1c2d3e4f-5a6b-7c8d-9e0f-1a2b3c4d5e6f"),
            Guid.Parse("0b9e6b6e-8f0a-4a1e-9c2d-3f4a5b6c7d8e"),
            50000, "USD", "inv-1");
        var withoutReference = withReference with { Reference = null };
        PaymentService.CreateRequestHash(withReference).Should().NotBe(PaymentService.CreateRequestHash(withoutReference));
    }

    [Fact]
    public void Hash_is_deterministic()
    {
        var command = new CreatePaymentCommand(Guid.NewGuid(), Guid.NewGuid(), 123, "EUR", "ref");
        PaymentService.CreateRequestHash(command).Should().Be(PaymentService.CreateRequestHash(command));
    }
}

public class PaymentProviderSimulatorTests
{
    [Theory]
    [InlineData(null, "success")]
    [InlineData("Rent share", "success")]
    [InlineData("Rent force-provider-failure", "failure")]
    [InlineData("Rent force-provider-timeout", "timeout")]
    [InlineData("Rent force-provider-duplicate", "duplicate")]
    [InlineData("Rent force-provider-delay", "delayed")]
    [InlineData("Rent force-provider-partial", "partial_failure")]
    [InlineData("force-provider-timeout please", "timeout")]
    public void Reference_markers_select_the_mode(string? reference, string expected) =>
        PaymentProviderSimulator.ModeForReference(reference).Should().Be(expected);
}

public class CustomerRolesTests
{
    [Fact]
    public void Parses_json_role_array() =>
        CustomerRoles.Parse("[\"admin\",\"fraud_analyst\"]").Should().BeEquivalentTo("admin", "fraud_analyst");

    [Fact]
    public void Null_or_invalid_defaults_to_customer()
    {
        CustomerRoles.Parse(null).Should().BeEquivalentTo("customer");
        CustomerRoles.Parse("").Should().BeEquivalentTo("customer");
        CustomerRoles.Parse("{not json").Should().BeEquivalentTo("customer");
    }

    [Fact]
    public void Serializes_to_json_array() =>
        CustomerRoles.Serialize(["admin"]).Should().Be("[\"admin\"]");
}
