using FinCore.Application;
using FinCore.Domain;
using FinCore.Infrastructure;
using FluentAssertions;
using Xunit;

namespace FinCore.Tests;

public class BloomFilterTests
{
    private sealed class NoCustomers : ICustomerIdentityRepository
    {
        public Task<CustomerIdentity?> FindByEmailAsync(string normalizedEmail, CancellationToken cancellationToken) =>
            Task.FromResult<CustomerIdentity?>(null);
        public Task<CustomerIdentity?> FindByIdAsync(Guid id, CancellationToken cancellationToken) =>
            Task.FromResult<CustomerIdentity?>(null);
        public Task<CustomerIdentity> CreateAsync(string normalizedEmail, string passwordHash, string? rolesJson = null,
            CancellationToken cancellationToken = default) =>
            Task.FromResult(new CustomerIdentity(Guid.NewGuid(), normalizedEmail, passwordHash, "pending_verification", ["customer"]));
    }

    private sealed class NoTokens : IAccessTokenIssuer
    {
        public AccessToken Issue(Guid customerId, IEnumerable<string>? roles = null) => new("token", 900);
    }

    [Fact]
    public void Positions_match_the_python_implementation()
    {
        // tests/test_bloom.py asserts the same vector, so both APIs set the same bits.
        new BloomFilter(1000, 4).Positions("password1234").Should().Equal(336, 727, 118, 509);
    }

    [Fact]
    public void Sizing_follows_the_optimal_formulas()
    {
        var bloom = BloomFilter.ForCapacity(1_000_000, 0.001);
        bloom.BitCount.Should().Be(14_377_588);
        bloom.HashCount.Should().Be(10);
    }

    [Fact]
    public void There_are_no_false_negatives_and_few_false_positives()
    {
        var members = Enumerable.Range(0, 10_000).Select(n => $"member-{n}").ToArray();
        var bloom = BloomFilter.Of(members, 0.01);
        members.Should().OnlyContain(member => bloom.MightContain(member));
        var falsePositives = Enumerable.Range(0, 50_000).Count(n => bloom.MightContain($"stranger-{n}"));
        (falsePositives / 50_000.0).Should().BeLessThan(0.02, "the target is 1%, with room for noise");
    }

    [Fact]
    public void Bundled_list_catches_common_passwords_case_insensitively()
    {
        var list = new CommonPasswordList(CommonPasswordList.ReadBundled());
        list.EntryCount.Should().BeGreaterThan(500);
        list.IsCommon("password1234").Should().BeTrue();
        list.IsCommon("Password1234").Should().BeTrue();
        list.IsCommon("  QWERTYUIOP123 ").Should().BeTrue();
        list.IsCommon("integration-password-123").Should().BeFalse();
    }

    [Fact]
    public async Task Registration_refuses_a_common_password()
    {
        var auth = new AuthenticationService(new NoCustomers(), new CustomerPasswordHasher(), new NoTokens(),
            new CommonPasswordList(CommonPasswordList.ReadBundled()));
        var register = () => auth.RegisterAsync("bloom@example.com", "Password1234", default);
        (await register.Should().ThrowAsync<AuthenticationException>())
            .Which.Code.Should().Be("password_too_common");
    }
}
