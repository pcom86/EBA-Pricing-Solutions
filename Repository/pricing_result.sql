USE [GapPricingPoC]
GO
/****** Object:  Table [dbo].[pricing_result]    Script Date: 11/14/2026 10:12:51 AM ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
CREATE TABLE [dbo].[pricing_result](
	[run_id] [uniqueidentifier] NOT NULL,
	[member_id] [uniqueidentifier] NOT NULL,
	[product] [nvarchar](50) NOT NULL,
	[category_number] [int] NULL,
	[status] [nvarchar](20) NULL,
	[age] [float] NULL,
	[nra_final] [float] NULL,
	[ceasing_age] [float] NULL,
	[product_active] [bit] NULL,
	[rate_basic] [float] NULL,
	[pre_tax_cover] [float] NULL,
	[total_cover] [float] NULL,
	[basic_technical_premium] [float] NULL,
	[co_pct] [float] NULL,
	[co_premium] [float] NULL,
	[waiver_premium] [float] NULL,
	[prb_premium] [float] NULL,
	[qx] [float] NULL,
	[profit_margin_amount] [float] NULL,
	[total_annual_premium] [float] NULL,
	[reporting_incidence] [float] NULL,
	[reporting_incidence_x_cover] [float] NULL,
PRIMARY KEY CLUSTERED 
(
	[run_id] ASC,
	[member_id] ASC,
	[product] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
GO
ALTER TABLE [dbo].[pricing_result]  WITH CHECK ADD FOREIGN KEY([member_id])
REFERENCES [dbo].[member_identity] ([member_id])
GO
ALTER TABLE [dbo].[pricing_result]  WITH CHECK ADD FOREIGN KEY([run_id])
REFERENCES [dbo].[run] ([run_id])
GO
