USE [GapPricingPoC]
GO
/****** Object:  Table [dbo].[scheme_product_config]    Script Date: 11/14/2026 10:12:51 AM ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
CREATE TABLE [dbo].[scheme_product_config](
	[scheme_id] [uniqueidentifier] NOT NULL,
	[product] [nvarchar](100) NOT NULL,
	[is_priced_product] [bit] NOT NULL,
	[rating] [nvarchar](50) NULL,
	[pricing_basis] [float] NULL,
	[fees_priced] [float] NULL,
	[basic_fees] [float] NULL,
	[profit_refund_loading_by_product] [float] NULL,
	[rate_basis_by_product] [float] NULL,
	[express_over_gla] [bit] NULL,
	[category_split] [nvarchar](50) NULL,
	[scheme_codes] [nvarchar](50) NULL,
 CONSTRAINT [PK_scheme_product_config_v2] PRIMARY KEY CLUSTERED 
(
	[scheme_id] ASC,
	[product] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
GO
ALTER TABLE [dbo].[scheme_product_config] ADD  DEFAULT ((0)) FOR [is_priced_product]
GO
ALTER TABLE [dbo].[scheme_product_config]  WITH CHECK ADD  CONSTRAINT [FK_scheme_product_config_v2_scheme] FOREIGN KEY([scheme_id])
REFERENCES [dbo].[scheme] ([scheme_id])
GO
ALTER TABLE [dbo].[scheme_product_config] CHECK CONSTRAINT [FK_scheme_product_config_v2_scheme]
GO
